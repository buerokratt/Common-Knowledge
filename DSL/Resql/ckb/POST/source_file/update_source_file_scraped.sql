SELECT copy_row_with_modifications(
    'source_file',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'url', '::TEXT', :url,
        'page_title', '::TEXT', :page_title,
        'original_data_url', '::TEXT', :original_data_url,
        'original_metadata_url', '::TEXT', :original_metadata_url,
        'original_data_hash', '::TEXT', :original_data_hash,
        'last_scraped_at', '::TIMESTAMP WITH TIME ZONE', :scraped_at::TEXT,
        'status', '::SOURCE_FILE_STATUS_TYPE', 'cleaning',
        'external_id', '::TEXT', :external_id,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM source_file
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM source_file 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;