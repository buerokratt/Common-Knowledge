SELECT copy_row_with_modifications(
    'source_file',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'cleaned_data_url', '::TEXT', :cleaned_data_url,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
        'status', '::source_file_status_type', 'finished'::source_file_status_type
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