/*
declaration:
  version: 0.1
  description: "Get the agency if its latest data hash or presigned url must be sent to CentOps"
  method: get
  returns: json
  namespace: agency
  allowlist: {}
  response:
    fields:
      - field: base_id
        type: string
        description: "Agency base ID"
*/
WITH latest_records AS (
    SELECT DISTINCT ON (base_id) base_id, data_hash, zipped_data_url, is_zipping, is_deleted
    FROM agency_management.agency
    ORDER BY base_id, updated_at DESC
)
SELECT
    l.base_id::text
FROM latest_records l
LEFT JOIN agency_management.centops_report r ON r.agency_base_id = l.base_id
WHERE l.is_deleted = FALSE
  AND l.zipped_data_url IS NOT NULL
  AND l.data_hash IS NOT NULL
  AND l.is_zipping = FALSE
  AND (
      r.id IS NULL
      OR r.reported_data_hash <> l.data_hash
      OR r.presigned_url_expires_at <= NOW() + INTERVAL '1 hour'
  )
LIMIT 1;
