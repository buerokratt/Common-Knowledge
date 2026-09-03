/*
declaration:
  version: 0.1
  description: "Reset is_zipping flag to FALSE and set zip_dirty to TRUE for the latest agency record by base_id, allowing the cron to retry zipping"
  method: post
  accepts: json
  returns: json
  namespace: agency
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Agency base ID"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
*/
SELECT copy_row_with_modifications(
    'agency_management.agency',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'is_zipping', '::BOOLEAN', 'FALSE',
        'zip_dirty', '::BOOLEAN', 'TRUE',
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM agency_management.agency
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at)
      FROM agency_management.agency
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;
