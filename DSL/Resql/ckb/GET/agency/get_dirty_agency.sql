/*
declaration:
  version: 0.1
  description: "get agency to create zip file for"
  method: get
  namespace: agency
  returns: json
  allowlist:
    query:
      - field: base_id
        type: string
        description: "agency base id"
  response:
    fields:
      - field: base_id
        type: string
        description: "base id of agency"
*/

WITH latest_records AS (
    SELECT DISTINCT ON (base_id) base_id, zip_dirty, is_zipping, is_deleted
    FROM agency_management.agency
    ORDER BY base_id, updated_at DESC
)
SELECT base_id::text
FROM latest_records
WHERE is_deleted = FALSE AND zip_dirty = TRUE AND is_zipping = FALSE
LIMIT 1;
