/*
declaration:
  version: 0.1
  description: "Fetch the latest configuration value for one key in one chatbot widget domain"
  method: get
  namespace: config
  returns: json
  allowlist:
    query:
      - field: key
        type: string
        description: "Configuration key to read, e.g. llm_module_active"
      - field: domain
        type: string
        description: "Chatbot widget domain UUID (widget_domains.domain_id)"
  response:
    fields:
      - field: domain_known
        type: boolean
        description: "Whether the domain's latest widget_domains row is active"
      - field: value
        type: string
        description: "Latest non-deleted value for the key in the domain; null if none"
*/
-- Always exactly one row. Reads the chatbot's byk database (resql-users).
-- "Latest" is the highest id, as in the chatbot's own get-domain-configuration:
-- a deleted latest row means no value, never the row before it.
-- The domain is compared as text, so a malformed UUID matches nothing
-- (domain_known = false) instead of failing the cast.
SELECT
    EXISTS (
        SELECT 1
        FROM (
            SELECT wd.active
            FROM widget_domains AS wd
            WHERE wd.domain_id::TEXT = LOWER(:domain::TEXT)
            ORDER BY wd.created DESC, wd.id DESC
            LIMIT 1
        ) AS latest_domain
        WHERE latest_domain.active = TRUE
    ) AS domain_known,
    (
        SELECT latest_config.value
        FROM (
            SELECT
                c.value,
                c.deleted
            FROM configuration AS c
            WHERE
                c.key = :key::TEXT
                AND c.domain::TEXT = LOWER(:domain::TEXT)
            ORDER BY c.id DESC
            LIMIT 1
        ) AS latest_config
        WHERE NOT latest_config.deleted
    ) AS value
