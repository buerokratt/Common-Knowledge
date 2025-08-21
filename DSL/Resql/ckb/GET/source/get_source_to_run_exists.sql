/*
declaration:
  version: 0.1
  description: "Get scheduled source to run exists"
  method: get
  returns: json
  namespace: scheduler
  allowlist: {}
  response:
    fields:
      - field: exists
        type: string
        description: "at least one scheduled record to run exists"
*/
SELECT count(*) > 0 AS exists
FROM data_collection.source
WHERE (base_id, updated_at) IN (
    SELECT base_id, max(updated_at) FROM data_collection.source
    GROUP BY base_id
)
    AND is_deleted = FALSE
    AND (
        status = 'new'
        OR (
            update_automatically = TRUE
            AND status NOT IN ('running', 'failed')
            AND next_scrapping_at <= NOW()
        )
    );