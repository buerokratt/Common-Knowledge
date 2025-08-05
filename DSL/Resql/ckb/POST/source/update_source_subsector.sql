SELECT copy_row_with_modifications(
    'source',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'subsector', '::TEXT', :subsector,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM source
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM source 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;