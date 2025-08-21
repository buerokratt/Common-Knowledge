/*
declaration:
  version: 0.1
  description: "Update cleaned URLs and mark source_file as finished by base_id"
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
      - field: cleaned_metadata_url
        type: string
        description: "URL of cleaned metadata"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
      - field: page_title
        type: string
        description: "Page title"
      - field: file_name
        type: string
        description: "File name"
      - field: url
        type: string
        description: "Original URL"
      - field: subsector
        type: string
        description: "Subsector"
      - field: source_base_id
        type: string
        description: "Source base ID"
      - field: base_id
        type: string
        description: "Base ID"
*/
SELECT copy_row_with_modifications(
    'data_collection.source_file',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'cleaned_data_url', '', :cleaned_data_url,
        'cleaned_metadata_url', '', :cleaned_metadata_url,
        'status', '::SOURCE_FILE_STATUS_TYPE', 'finished',
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id, page_title, file_name, url, subsector, source_base_id, base_id
FROM data_collection.source_file
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM data_collection.source_file 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;