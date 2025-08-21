/*
declaration:
  version: 0.1
  description: "Update zipping status and reset zip_dirty flag of the latest agency record by base_id"
  method: post
  accepts: json
  returns: json
  namespace: agency
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Agency base ID"
      - field: zipping
        type: boolean
        description: "Zipping status flag"
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
        'is_zipping', '::BOOLEAN', :zipping,
        'zip_dirty', '::BOOLEAN', 'FALSE',
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