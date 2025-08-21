/*
declaration:
  version: 0.1
  description: "Update scraping finish time and log URLs of the latest source_run_report by base_id"
  method: post
  accepts: json
  returns: json
  namespace: source_run_report
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Source run report base ID"
      - field: scraping_finished_at
        type: string
        description: "Scraping finish timestamp"
      - field: scraping_log_url
        type: string
        description: "Scraping log URL"
      - field: cleaning_log_url
        type: string
        description: "Cleaning log URL"
*/
SELECT copy_row_with_modifications(
       'monitoring.source_run_report',
       'id', '::UUID', id::VARCHAR,
       ARRAY[
           'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
           'scraping_finished_at', '::TIMESTAMP WITH TIME ZONE', :scraping_finished_at,
           'scraping_log_url', '', :scraping_log_url,
           'cleaning_log_url', '', :cleaning_log_url
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