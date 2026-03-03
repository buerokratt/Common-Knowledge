storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}

api_addr = "http://vault:8200"

disable_mlock = false
disable_cache = false
ui            = false

default_lease_ttl = "168h"
max_lease_ttl     = "720h"

log_level  = "INFO"
log_format = "json"