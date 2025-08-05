SELECT copy_row_with_modifications(
    'source_file',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'is_deleted', '::BOOLEAN', 'TRUE',
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id, source_base_id, agency_base_id
FROM source_file
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM source_file 
      WHERE base_id = :base_id::UUID
  );