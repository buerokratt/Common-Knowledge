/*
declaration:
  version: 0.1
  description: "Get 1 not scheduled source"
  method: get
  returns: json
  namespace: source
  allowlist: {}
  response:
    fields:
      - field: base_id
        type: string
        description: "base_id of source"
      - field: cron_schedule
        type: string
        description: "cron schedule"
*/
WITH latest_records AS (
    SELECT DISTINCT ON (base_id) base_id, cron_schedule, is_deleted, update_automatically, next_scrapping_at, status
    FROM data_collection.source
    ORDER BY base_id, updated_at DESC
)
SELECT 
    base_id::text,
    cron_schedule
FROM latest_records
WHERE is_deleted = FALSE 
  AND update_automatically = TRUE 
  AND next_scrapping_at IS NULL
  AND status NOT IN ('running', 'failed')
LIMIT 1;