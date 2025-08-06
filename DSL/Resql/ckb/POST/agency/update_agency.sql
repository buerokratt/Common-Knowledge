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
      - field: external_id
        type: string
        description: "External identifier"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
*/
SELECT copy_row_with_modifications(
    'agency',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'name', '::TEXT', :name,
        'sector', '::TEXT', :sector,
        'external_id', '::TEXT', :external_id,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM agency
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM agency 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;