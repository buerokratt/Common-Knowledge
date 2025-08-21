/*
declaration:
  version: 0.1
  description: "Increment error count and update timestamp of the latest source_run_report by base_id"
  method: post
  accepts: json
  returns: json
  namespace: source_run_report
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Source run report base ID"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
*/
SELECT copy_row_with_modifications(
       'monitoring.source_run_report',
       'id', '::UUID', id::VARCHAR,
       ARRAY[
           'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
           'errors', '::INTEGER', (errors + 1)::VARCHAR
       ]::VARCHAR[]
)
FROM monitoring.source_run_report
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at)
      FROM monitoring.source_run_report
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;