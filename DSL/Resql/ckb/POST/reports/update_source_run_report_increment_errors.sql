SELECT copy_row_with_modifications(
       'source_run_report',
       'id', '::UUID', id::VARCHAR,
       ARRAY[
           'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
           'errors', '::INTEGER', (errors + 1)::VARCHAR
       ]::VARCHAR[]
)
FROM source_run_report
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at)
      FROM source_run_report
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;