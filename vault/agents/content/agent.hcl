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
  # token is picked up without a restart. The Azure Blob credential (Stage H)
  # is read through the same token, on every store build.
  sink "file" {
    config = {
      path = "/agent/out/token"
      mode = 0640
    }
  }
}

# Deliberately no cache, listener or api_proxy block: the agent's only job is
# to write and renew the token. Vault 1.21.3 (the image compose runs) REFUSES
# a `cache` block with no listener or template — "enabling the cache requires
# at least 1 template or 1 listener to be defined" — so the agent would exit
# at startup and no token would ever be written. Checked against that image.
