/*
declaration:
  version: 0.1
  description: "Mark source_run_page rows as deleted by source_run_report_base_id"
  method: post
  accepts: json
  returns: json
  namespace: source_run_page
  allowlist:
    body:
      - field: source_run_report_base_id
        type: string
        description: "Source run report base ID"
*/

SELECT copy_row_with_modifications(
    'monitoring.source_run_page',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'is_deleted', '::BOOLEAN', 'TRUE',
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM monitoring.source_run_page
WHERE source_run_report_base_id = :source_run_report_base_id::UUID
  AND (base_id, updated_at) IN (
      SELECT base_id, MAX(updated_at)
      FROM monitoring.source_run_page
      WHERE source_run_report_base_id = :source_run_report_base_id::UUID
      GROUP BY base_id
  )
  AND is_deleted = FALSE;