/*
declaration:
  version: 0.1
  description: "Update data_hash, zipped_data_url and zipping status of the latest agency record by base_id"
  method: post
  accepts: json
  returns: json
  namespace: agency
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Agency base ID"
      - field: data_hash
        type: string
        description: "Data hash value"
      - field: zip_data_url
        type: string
        description: "URL of zipped data"
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
        'data_hash', '', :data_hash,
        'zipped_data_url', '', :zip_data_url,
        'is_zipping', '::BOOLEAN', 'FALSE',
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