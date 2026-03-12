# Vault Setup — Cleaning Service

This document describes the Vault integration for the cleaning service, including architecture, file structure, startup flow, and how to manage secrets.

## Architecture

```
┌─────────────────┐    ┌──────────────────────┐    ┌─────────────────┐
│   Vault Server  │    │  Vault Agent (cleaner)│    │ Cleaning Server │
│                 │    │                       │    │                 │
│  - KV v2 engine │◄───│  - AppRole auth       │    │  - Reads token  │
│  - AppRole auth │    │  - Auto token renewal │───►│    from file    │
│  - Policies     │    │  - Writes token to    │    │  - Calls vault  │
│                 │    │    /agent/out/token   │    │    directly     │
└─────────────────┘    └──────────────────────┘    └─────────────────┘
```

The Vault Agent authenticates with Vault using AppRole credentials and writes a continuously renewed token to a shared volume. The cleaning server reads this token file on each task and uses it to fetch secrets directly from Vault. This means:

* The cleaning server never holds long-lived credentials
* Token rotation is handled automatically by the agent
* Vault is not exposed outside the Docker network

## File Structure

```
vault/
├── config.hcl                  # Vault server configuration (storage, listener, TTLs)
├── init.sh                     # One-shot init script (runs in vault-init container)
└── agents/
    └── cleaner/
        └── agent.hcl           # Vault Agent configuration for the cleaning service
```

All credentials and tokens are written to Docker volumes at runtime — nothing sensitive is stored on disk.

## Docker Volumes

| Volume                | Contents                            | Accessible by                                            |
| --------------------- | ----------------------------------- | -------------------------------------------------------- |
| `vault-data`        | Vault storage + unseal keys         | `vault`,`vault-init`                                 |
| `vault-agent-creds` | AppRole role_id and secret_id files | `vault-init`(write),`vault-agent-cleaner`(read)      |
| `vault-agent-token` | Vault token file                    | `vault-agent-cleaner`(write),`cleaning-server`(read) |
| `vault-logs`        | Vault server logs                   | `vault`                                                |

## Startup Flow

The containers start in this dependency order:

1. **`vault`** — starts the Vault server, waits to be healthy (HTTP 200/501)
2. **`vault-init`** — runs once and exits:
   * On first run: initializes Vault, unseals it, enables KV v2 and AppRole, creates policies and roles, writes AppRole credentials to `vault-agent-creds`, writes a placeholder secret
   * On subsequent runs: unseals Vault if sealed, rotates all AppRole secret IDs
3. **`vault-agent-cleaner`** — starts after `vault-init` exits successfully, authenticates using AppRole credentials and continuously writes a renewed token to `vault-agent-token`
4. **`cleaning-server`** — starts after `vault-agent-cleaner` is healthy (token file is non-empty)

## Secret Structure

Secrets are stored in KV v2 under `secret/` with this path layout:

```
secret/
├── llm/
│   └── connections/
│       └── azure_openai/
│           └── cleaner        # Azure OpenAI credentials for the cleaning service
└── encryption/
    ├── public_key
    └── private_key
```

### Cleaner secret fields

| Field           | Description                                 |
| --------------- | ------------------------------------------- |
| `api_key`     | Azure OpenAI API key                        |
| `endpoint`    | Azure OpenAI endpoint URL                   |
| `deployment`  | Model deployment name (e.g.`gpt-4o-mini`) |
| `api_version` | API version (e.g.`2024-02-01`)            |

## Policies

| Policy                       | Role                          | Access                                                              |
| ---------------------------- | ----------------------------- | ------------------------------------------------------------------- |
| `cleaner-policy`           | `cleaner-service`           | Read `secret/data/llm/connections/*`only                          |
| `gui-policy`               | `gui-service`               | Read public key only                                                |
| `cron-manager-policy`      | `cron-manager-service`      | Full access to LLM and embeddings connections, read encryption keys |
| `llm-orchestration-policy` | `llm-orchestration-service` | Read LLM and embeddings connections                                 |

## Managing Secrets

Vault has no host port exposed. All secret management is done via `docker exec`.

**Get the root token:**

```bash
ROOT_TOKEN=$(docker exec vault cat /vault/file/unseal-keys.json \
  | grep -o '"root_token":"[^"]*"' | cut -d'"' -f4)
```

**Write or update the cleaner secret:**

```bash
docker exec vault sh -c "VAULT_TOKEN=$ROOT_TOKEN vault kv put \
  secret/llm/connections/azure_openai/cleaner \
  api_key=YOUR_KEY \
  endpoint=https://YOUR_RESOURCE.openai.azure.com/ \
  deployment=gpt-4o-mini \
  api_version=2024-02-01"
```

**Read a secret:**

```bash
docker exec vault sh -c "VAULT_TOKEN=$ROOT_TOKEN vault kv get \
  secret/llm/connections/azure_openai/cleaner"
```

**Check the agent token is being written:**

```bash
docker exec vault-agent-cleaner cat /agent/out/token
```

**Check Vault seal status:**

```bash
docker exec vault vault status
```

## How the Cleaning Service Uses Vault

On every cleaning task, `config.py` calls `get_vault_secrets()` which:

1. Reads the current token from `/agent/out/token` (written by Vault Agent)
2. Makes a GET request to `http://vault:8200/v1/secret/data/llm/connections/azure_openai/cleaner` with the token as the `X-Vault-Token` header
3. Returns the secrets as a `VaultSecrets` object with `api_key` stored as a `SecretStr` (never logged)

Fetching fresh on every task means token rotation by the agent is always respected — the cleaning server never caches stale credentials.

## Troubleshooting

**`vault-init` exits with error**

Check the logs:

```bash
docker logs vault-init
```

Common causes: Vault not reachable (check `vault` container health), or `jq`/`curl` failed to install (check network access from the init container).

**`vault-agent-cleaner` unhealthy (token file empty)**

The agent failed to authenticate. Check its logs:

```bash
docker logs vault-agent-cleaner
```

Usually means the AppRole credentials in `vault-agent-creds` are stale (e.g. Vault was re-initialized but the volume was not cleared). Fix by removing the volume and restarting:

```bash
docker compose down
docker volume rm vault-agent-creds vault-data vault-agent-token
docker compose up vault vault-init vault-agent-cleaner cleaning-server
```

**Cleaning server cannot fetch secrets**

Check that the token file is non-empty and the secret exists:

```bash
docker exec vault-agent-cleaner cat /agent/out/token
docker exec vault sh -c "VAULT_TOKEN=$ROOT_TOKEN vault kv get secret/llm/connections/azure_openai/cleaner"
```

**Vault is sealed after restart**

`vault-init` handles unsealing automatically on subsequent runs. If it has already exited and Vault is sealed, run it manually:

```bash
docker compose up vault-init
```
