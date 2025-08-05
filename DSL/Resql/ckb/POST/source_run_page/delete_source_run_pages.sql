SELECT copy_row_with_modifications(
    'source_run_page',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'is_deleted', '::BOOLEAN', 'TRUE',
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM source_run_page
WHERE source_run_report_base_id = :source_run_report_base_id::UUID
  AND (base_id, updated_at) IN (
      SELECT base_id, MAX(updated_at)
      FROM source_run_page
      WHERE source_run_report_base_id = :source_run_report_base_id::UUID
      GROUP BY base_id
  )
  AND is_deleted = FALSE;