SELECT copy_row_with_modifications(
    'agency',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'name', '::TEXT', :name,
        'sector', '::TEXT', :sector,
        'external_id', '::TEXT', :external_id,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM agency
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM agency 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;