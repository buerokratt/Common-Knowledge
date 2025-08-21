/*
declaration:
  version: 0.1
  description: "Mark the latest agency record as deleted by base_id"
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
      - field: zipped_data_url
        type: string
        description: "Zipped data URL"
*/
SELECT copy_row_with_modifications(
    'agency_management.agency',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'is_deleted', '::BOOLEAN', 'TRUE',
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id, zipped_data_url
FROM agency_management.agency
WHERE base_id = :base_id::UUID
  AND is_deleted = FALSE
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM agency_management.agency 
      WHERE base_id = :base_id::UUID
  );