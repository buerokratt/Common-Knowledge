/*
declaration:
  version: 0.1
  description: "list source run pages"
  method: get
  namespace: source_run_pages
  returns: json
  allowlist:
    query:
      - field: source_run_report_base_id
        type: string
        description: "base id of report"
      - field: sorting
        type: string
        description: "sorting"
        enum: ["url asc", "url desc", "error_type asc", "error_type desc", "error_message asc", "error_message desc", "scraped_at asc", "scraped at desc"]
      - field: page_size
        type: number
        description: "page size"
      - field: page
        type: number
        description: "page number"
  response:
    fields:
      - field: id
        type: integer
        description: "Primary key of the report entry"
      - field: base_id
        type: string
        description: "base id of source run page page"
      - field: source_run_report_base_id
        type: string
        description: "report base id"
      - field: url
        type: string
        description: "scraped url"
      - field: error_type
        type: string
        description: "error type"
      - field: error_message
        type: string
        description: "error message"
      - field: scraped_at
        type: timestamp
        description: "when scraped"
      - field: page
        type: number
        description: "page number"
      - field: total_pages
        type: number
        description: "number of pages"
      - field: total
        type: number
        description: "total number of agencies"
*/
WITH latest_run_pages AS (
    SELECT DISTINCT ON (base_id)
        id, base_id, source_run_report_base_id, url, error_type, error_message, scraped_at, is_deleted, updated_at
    FROM monitoring.source_run_page
    WHERE (:source_run_report_base_id IS NULL OR source_run_report_base_id = :source_run_report_base_id::UUID)
    ORDER BY base_id, updated_at DESC
)
SELECT 
    id, base_id, source_run_report_base_id, url, error_type, error_message, scraped_at,
    :page as page,
    CEIL(COUNT(*) OVER () / :page_size::DECIMAL) AS total_pages,
    (COUNT(*) OVER ()) AS total
FROM latest_run_pages
WHERE is_deleted = FALSE
ORDER BY 
    CASE WHEN :sorting = 'url asc' THEN url END ASC,
    CASE WHEN :sorting = 'url desc' THEN url END DESC,
    CASE WHEN :sorting = 'error_type asc' THEN error_type END ASC,
    CASE WHEN :sorting = 'error_type desc' THEN error_type END DESC,
    CASE WHEN :sorting = 'error_message asc' THEN error_message END ASC,
    CASE WHEN :sorting = 'error_message desc' THEN error_message END DESC,
    CASE WHEN :sorting = 'scraped_at asc' THEN scraped_at END ASC,
    CASE WHEN :sorting = 'scraped_at desc' THEN scraped_at END DESC,
    updated_at DESC NULLS LAST
LIMIT :page_size::INTEGER 
OFFSET ((GREATEST(:page::INTEGER, 1) - 1) * :page_size::INTEGER);