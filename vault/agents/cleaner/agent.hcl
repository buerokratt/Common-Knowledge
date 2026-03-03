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

# No cache or listener block — the agent's only job is to authenticate
# and keep the token in /agent/out/token renewed.
# Services talk to vault:8200 directly using that token.