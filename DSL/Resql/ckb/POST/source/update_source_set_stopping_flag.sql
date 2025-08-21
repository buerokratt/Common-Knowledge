/*
declaration:
  version: 0.1
  description: "Mark the latest running source record as stopping by base_id"
  method: post
  accepts: json
  returns: json
  namespace: source
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Source base ID"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
*/
SELECT copy_row_with_modifications(
    'data_collection.source',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
        'is_stopping', '::BOOLEAN', TRUE::VARCHAR
    ]::VARCHAR[]
) as id
FROM data_collection.source
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at)
      FROM data_collection.source
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE AND status = 'running';