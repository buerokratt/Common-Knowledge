vault {
  address = "http://vault-test:8200"
  retry {
    num_retries = 10
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

  sink "file" {
    config = {
      path = "/agent/out/token"
      mode = 0644
    }
  }
}

# Vault Agent 1.16+ requires at least one of: cache, listener, or api_proxy
# alongside auto_auth. A minimal cache block satisfies this requirement.
cache {
  use_auto_auth_token = true
}
