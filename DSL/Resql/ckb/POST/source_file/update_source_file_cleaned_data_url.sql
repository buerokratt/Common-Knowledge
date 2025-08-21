/*
declaration:
  version: 0.1
  description: "Update cleaned_data_url and mark source_file as finished by base_id"
  method: post
  accepts: json
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Source file base ID"
      - field: cleaned_data_url
        type: string
        description: "URL of cleaned data"
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
        'cleaned_data_url', '::TEXT', :cleaned_data_url,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
        'status', '::source_file_status_type', 'finished'::source_file_status_type
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