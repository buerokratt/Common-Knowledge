/*
declaration:
  version: 0.1
  description: "Update agency record fields by base_id"
  method: post
  accepts: json
  returns: json
  namespace: agency
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Agency base ID"
      - field: name
        type: string
        description: "Agency name"
      - field: sector
        type: string
        description: "Agency sector"
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
        'name', '::TEXT', :name,
        'sector', '::TEXT', :sector,
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