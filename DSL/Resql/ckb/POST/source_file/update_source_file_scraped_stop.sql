SELECT copy_row_with_modifications(
    'source_file',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'status', '::SOURCE_FILE_STATUS_TYPE', :status,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
        'last_scraped_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
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
