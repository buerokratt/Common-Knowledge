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

  # Writes a valid, auto-renewed token to a file.
  # content-external reads this file directly.
  sink "file" {
    config = {
      path = "/agent/out/token"
      mode = 0640
    }
  }
}
