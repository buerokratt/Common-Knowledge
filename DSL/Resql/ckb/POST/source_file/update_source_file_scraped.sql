/*
declaration:
  version: 0.1
  description: "Update source_file fields and set status to 'cleaning' by base_id"
  method: post
  accepts: json
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Source file base ID"
      - field: url
        type: string
        description: "URL"
      - field: page_title
        type: string
        description: "Page title"
      - field: original_data_url
        type: string
        description: "Original data URL"
      - field: original_metadata_url
        type: string
        description: "Original metadata URL"
      - field: original_data_hash
        type: string
        description: "Original data hash"
      - field: scraped_at
        type: string
        description: "Last scraped timestamp"
      - field: external_id
        type: string
        description: "External identifier"
      - field: status
        type: string
        description: "Processing status (e.g. in_review, scraping, cleaning, finished)"
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
        'url', '::TEXT', :url,
        'page_title', '::TEXT', :page_title,
        'original_data_url', '::TEXT', :original_data_url,
        'original_metadata_url', '::TEXT', :original_metadata_url,
        'original_data_hash', '::TEXT', :original_data_hash,
        'last_scraped_at', '::TIMESTAMP WITH TIME ZONE', :scraped_at::TEXT,
        'status', '::SOURCE_FILE_STATUS_TYPE', :status,
        'external_id', '::TEXT', :external_id,
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