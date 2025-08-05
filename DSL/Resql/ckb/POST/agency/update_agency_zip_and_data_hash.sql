SELECT copy_row_with_modifications(
    'agency',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'data_hash', '', :data_hash,
        'zipped_data_url', '', :zip_data_url,
        'is_zipping', '::BOOLEAN', 'FALSE',
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