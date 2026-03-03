/*
declaration:
  version: 0.1
  description: "Get latest source run report by source base id"
  method: get
  namespace: reports
  returns: json
  allowlist:
    query:
      - field: source_base_id
        type: string
        description: "source base id"
  response:
    fields:
      - field: id
        type: integer
        description: "Primary key of the report entry"
      - field: base_id
        type: string
        description: "base id of report"
      - field: source_base_id
        type: string
        description: "source base id"
      - field: scraping_log_url
        type: string
        description: "storage link for scraping log"
*/
SELECT
    id, base_id, source_base_id, scraping_log_url
FROM monitoring.source_run_report
WHERE source_base_id = :source_base_id::UUID
  AND is_deleted = FALSE
ORDER BY updated_at DESC
LIMIT 1;