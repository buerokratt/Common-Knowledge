/*
declaration:
  version: 0.1
  description: "Update status of source_file by base_id"
  method: post
  accepts: json
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Source file base ID"
      - field: status
        type: string
        description: "Status to set (source_file_status_type)"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
*/
SELECT copy_row_with_modifications(
    'data_collection.source_file',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'status', '::source_file_status_type', :status::source_file_status_type,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM data_collection.source_file
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM data_collection.source_file 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;