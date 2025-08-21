/*
declaration:
  version: 0.1
  description: "Update edited URLs and timestamp of the latest source_file by base_id"
  method: post
  accepts: json
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Source file base ID"
      - field: edited_data_url
        type: string
        description: "Edited data URL"
      - field: edited_metadata_url
        type: string
        description: "Edited metadata URL"
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
      - field: agency_base_id
        type: string
        description: "Agency base ID"
*/
SELECT copy_row_with_modifications(
    'data_collection.source_file',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'edited_data_url', '', :edited_data_url,
        'edited_metadata_url', '', :edited_metadata_url,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id, page_title, file_name, url, subsector, source_base_id, agency_base_id
FROM data_collection.source_file
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at)
      FROM data_collection.source_file
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;