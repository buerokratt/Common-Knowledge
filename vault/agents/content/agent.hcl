vault {
  address = "http://vault:8200"
  retry {
    num_retries = 5
  }
}

pid_file = "/agent/out/pidfile"

auto_auth {
  method "approle" {
    mount_path = "auth/approle"
    config = {
      role_id_file_path                   = "/agent/credentials/content_role_id"
      secret_id_file_path                 = "/agent/credentials/content_secret_id"
      remove_secret_id_file_after_reading = false
    }
  }

  # Writes a valid, auto-renewed token to a file. content-external reads it
  # on every llm-module operation (L11) and never caches it, so a rotated
  # token is picked up without a restart. Stage H's Azure credential will be
  # read through the same token.
  sink "file" {
    config = {
      path = "/agent/out/token"
      mode = 0640
    }
  }
}

# Vault Agent 1.16+ requires at least one of: cache, listener, or api_proxy
# alongside auto_auth. A minimal cache block satisfies this without adding
# any proxy behaviour — the agent's only job is to write and renew the token.
cache {
  use_auto_auth_token = true
}
