/*
declaration:
  version: 0.1
  description: "get report logs by base id"
  method: get
  namespace: reports
  returns: json
  allowlist:
    query:
      - field: base_id
        type: string
        description: "agency base id"
  response:
    fields:
      - field: id
        type: integer
        description: "Primary key of the report entry"
      - field: base_id
        type: string
        description: "base id of report"
      - field: agency_name
        type: string
        description: "name of agency"
      - field: url
        type: string
        description: "source url"
      - field: scraping_log_url
        type: string
        description: "storage link for scraping log"
      - field: cleaning_log_url
        type: string
        description: "storage link for cleaning log"
*/
SELECT
    id, base_id, agency_name, url, scraping_log_url, cleaning_log_url
FROM monitoring.source_run_report 
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM monitoring.source_run_report 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;