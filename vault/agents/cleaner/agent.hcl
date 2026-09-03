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
      role_id_file_path                   = "/agent/credentials/cleaner_role_id"
      secret_id_file_path                 = "/agent/credentials/cleaner_secret_id"
      remove_secret_id_file_after_reading = false
    }
  }

  # Writes a valid, auto-renewed token to a file.
  # The cleaning-server reads this file directly.
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
