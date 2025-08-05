SELECT copy_row_with_modifications(
    'source_run_report',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'is_deleted', '::BOOLEAN', 'TRUE',
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM source_run_report
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM source_run_report 
      WHERE base_id = :base_id::UUID
  );