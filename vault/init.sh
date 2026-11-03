#!/bin/sh
set -e

VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
UNSEAL_FILE="/vault/data/unseal-keys.json"
INIT_FLAG="/vault/data/.initialized"
CREDS="/agent/credentials"

echo "=== Vault Init ==="

# ── wait for vault ──────────────────────────────────────────────

echo "Waiting for Vault..."
for i in $(seq 1 30); do
    # Any of these codes means Vault is reachable and ready to talk to:
    #   200 = healthy/unsealed
    #   429 = standby
    #   501 = not yet initialized  ← this is the normal state on first boot
    #   503 = sealed
    # -f is intentionally NOT used here so we get the status code on non-2xx responses.
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$VAULT_ADDR/v1/sys/health" 2>/dev/null || echo "000")
    case "$HTTP_CODE" in
        200|429|501|503)
            echo "Vault is ready (HTTP $HTTP_CODE)"
            break
            ;;
    esac
    echo "Waiting... ($i/30) (got: $HTTP_CODE)"
    sleep 2
done

# ── helpers ─────────────────────────────────────────────────────

vault_post() {
    curl -sf -X POST \
        -H "Content-Type: application/json" \
        -H "X-Vault-Token: $ROOT_TOKEN" \
        -d "$2" \
        "$VAULT_ADDR$1" || true
}

vault_put_policy() {
    POLICY_JSON=$(printf '%s' "$2" | jq -Rs '{"policy":.}')
    curl -sf -X POST \
        -H "Content-Type: application/json" \
        -H "X-Vault-Token: $ROOT_TOKEN" \
        -d "$POLICY_JSON" \
        "$VAULT_ADDR/v1/sys/policies/acl/$1" >/dev/null
    echo "Policy $1 written"
}

vault_get() {
    curl -sf \
        -H "X-Vault-Token: $ROOT_TOKEN" \
        "$VAULT_ADDR$1"
}

do_unseal() {
    echo "Unsealing..."
    for KEY in \
        "$(jq -r '.keys[0]' "$UNSEAL_FILE")" \
        "$(jq -r '.keys[1]' "$UNSEAL_FILE")" \
        "$(jq -r '.keys[2]' "$UNSEAL_FILE")"; do
        curl -sf -X POST \
            -H "Content-Type: application/json" \
            -d "{\"key\":\"$KEY\"}" \
            "$VAULT_ADDR/v1/sys/unseal" >/dev/null
    done
    sleep 2
    echo "Unsealed"
}

# ═══════════════════════════════════════════════════════════════
if [ ! -f "$INIT_FLAG" ]; then
# ═══════════════════════════════════════════════════════════════

    echo "=== FIRST TIME SETUP ==="

    echo "Initializing Vault..."
    curl -sf -X POST \
        -H "Content-Type: application/json" \
        -d '{"secret_shares":5,"secret_threshold":3}' \
        "$VAULT_ADDR/v1/sys/init" > "$UNSEAL_FILE"

    ROOT_TOKEN=$(jq -r '.root_token' "$UNSEAL_FILE")
    echo "Initialized"

    do_unseal

    # Enable KV v2
    vault_post "/v1/sys/mounts/secret" '{"type":"kv","options":{"version":"2"}}' >/dev/null
    echo "KV v2 enabled"

    # Enable AppRole
    vault_post "/v1/sys/auth/approle" '{"type":"approle"}' >/dev/null
    echo "AppRole enabled"

    # ── Policies ────────────────────────────────────────────────

    vault_put_policy "gui-policy" \
'path "secret/data/encryption/public_key" { capabilities = ["read"] }
path "secret/metadata/encryption/public_key" { capabilities = ["read"] }
path "secret/data/encryption/private_key" { capabilities = ["deny"] }
path "secret/data/llm/*" { capabilities = ["deny"] }
path "secret/data/embeddings/*" { capabilities = ["deny"] }'

    vault_put_policy "cron-manager-policy" \
'path "secret/data/encryption/public_key" { capabilities = ["read"] }
path "secret/metadata/encryption/public_key" { capabilities = ["read"] }
path "secret/data/encryption/private_key" { capabilities = ["read"] }
path "secret/metadata/encryption/private_key" { capabilities = ["read"] }
path "secret/data/llm/connections/*" { capabilities = ["create","read","update","delete"] }
path "secret/metadata/llm/connections/*" { capabilities = ["read","list","delete"] }
path "secret/data/embeddings/connections/*" { capabilities = ["create","read","update","delete"] }
path "secret/metadata/embeddings/connections/*" { capabilities = ["read","list","delete"] }
path "auth/token/lookup-self" { capabilities = ["read"] }'

    vault_put_policy "llm-orchestration-policy" \
'path "secret/data/llm/connections/*" { capabilities = ["read","list"] }
path "secret/metadata/llm/connections/*" { capabilities = ["read","list"] }
path "secret/data/embeddings/connections/*" { capabilities = ["read","list"] }
path "secret/metadata/embeddings/connections/*" { capabilities = ["read","list"] }
path "secret/data/encryption/*" { capabilities = ["deny"] }
path "auth/token/lookup-self" { capabilities = ["read"] }'

    vault_put_policy "cleaner-policy" \
'path "secret/data/llm/connections/*" { capabilities = ["read","list"] }
path "secret/metadata/llm/connections/*" { capabilities = ["read","list"] }
path "secret/data/encryption/*" { capabilities = ["deny"] }
path "auth/token/lookup-self" { capabilities = ["read"] }'

    # ── AppRoles ─────────────────────────────────────────────────

    vault_post "/v1/auth/approle/role/gui-service" \
        '{"token_policies":["gui-policy"],"token_no_default_policy":true,"token_ttl":"15m","token_max_ttl":"1h","secret_id_ttl":"24h","secret_id_num_uses":0}' >/dev/null
    vault_post "/v1/auth/approle/role/cron-manager-service" \
        '{"token_policies":["cron-manager-policy"],"token_no_default_policy":true,"token_ttl":"30m","token_max_ttl":"8h","secret_id_ttl":"24h","secret_id_num_uses":0}' >/dev/null
    vault_post "/v1/auth/approle/role/llm-orchestration-service" \
        '{"token_policies":["llm-orchestration-policy"],"token_no_default_policy":true,"token_ttl":"1h","token_max_ttl":"24h","secret_id_ttl":"24h","secret_id_num_uses":0}' >/dev/null
    vault_post "/v1/auth/approle/role/cleaner-service" \
        '{"token_policies":["cleaner-policy"],"token_no_default_policy":true,"token_ttl":"1h","token_max_ttl":"4h","secret_id_ttl":"24h","secret_id_num_uses":0}' >/dev/null
    echo "AppRoles created"

    # ── Credentials ──────────────────────────────────────────────

    mkdir -p "$CREDS"
    for ROLE in gui cron llm cleaner; do
        case "$ROLE" in
            gui)     RNAME="gui-service" ;;
            cron)    RNAME="cron-manager-service" ;;
            llm)     RNAME="llm-orchestration-service" ;;
            cleaner) RNAME="cleaner-service" ;;
        esac

        ROLE_ID=$(vault_get "/v1/auth/approle/role/$RNAME/role-id" | jq -r '.data.role_id')
        printf '%s' "$ROLE_ID" > "$CREDS/${ROLE}_role_id"

        SECRET_ID=$(vault_post "/v1/auth/approle/role/$RNAME/secret-id" '{}' | jq -r '.data.secret_id')
        printf '%s' "$SECRET_ID" > "$CREDS/${ROLE}_secret_id"

        chmod 640 "$CREDS/${ROLE}_role_id" "$CREDS/${ROLE}_secret_id"
        echo "Credentials written for $ROLE"
    done

    # ── Placeholder secret ───────────────────────────────────────

    vault_post "/v1/secret/data/llm/connections/azure_openai/cleaner" \
        '{"data":{"api_key":"REPLACE_ME","endpoint":"https://REPLACE_ME.openai.azure.com/","deployment":"gpt-4o-mini","api_version":"2024-02-01"}}' >/dev/null
    echo "Placeholder cleaner secret created"

    touch "$INIT_FLAG"
    echo "=== First time setup complete ==="

# ═══════════════════════════════════════════════════════════════
else
# ═══════════════════════════════════════════════════════════════

    echo "=== SUBSEQUENT DEPLOYMENT ==="
    ROOT_TOKEN=$(jq -r '.root_token' "$UNSEAL_FILE")

    SEALED=$(curl -sf "$VAULT_ADDR/v1/sys/seal-status" | jq -r '.sealed')
    if [ "$SEALED" = "true" ]; then
        do_unseal
    else
        echo "Already unsealed"
    fi

    # Rotate secret_ids
    for ROLE in gui cron llm cleaner; do
        case "$ROLE" in
            gui)     RNAME="gui-service" ;;
            cron)    RNAME="cron-manager-service" ;;
            llm)     RNAME="llm-orchestration-service" ;;
            cleaner) RNAME="cleaner-service" ;;
        esac

        SECRET_ID=$(vault_post "/v1/auth/approle/role/$RNAME/secret-id" '{}' | jq -r '.data.secret_id')
        printf '%s' "$SECRET_ID" > "$CREDS/${ROLE}_secret_id"
        chmod 640 "$CREDS/${ROLE}_secret_id"

        if [ ! -f "$CREDS/${ROLE}_role_id" ]; then
            ROLE_ID=$(vault_get "/v1/auth/approle/role/$RNAME/role-id" | jq -r '.data.role_id')
            printf '%s' "$ROLE_ID" > "$CREDS/${ROLE}_role_id"
            chmod 640 "$CREDS/${ROLE}_role_id"
        fi
        echo "Rotated secret_id for $ROLE"
    done

fi
# ═══════════════════════════════════════════════════════════════

echo "=== Vault init complete ==="