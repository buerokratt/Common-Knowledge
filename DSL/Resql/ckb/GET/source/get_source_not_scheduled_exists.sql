/*
declaration:
  version: 0.1
  description: "Get not scheduled source exists"
  method: get
  returns: json
  namespace: scheduler
  allowlist: {}
  response:
    fields:
      - field: exists
        type: string
        description: "at least one not scheduled record exists"
*/
SELECT count(*) > 0 AS exists
FROM data_collection.source
WHERE (base_id, updated_at) IN (
    SELECT base_id, max(updated_at)
    FROM data_collection.source
    GROUP BY base_id
)
    AND is_deleted = FALSE
    AND update_automatically = TRUE
    AND next_scrapping_at IS NULL
    AND status NOT IN ('running', 'failed');
