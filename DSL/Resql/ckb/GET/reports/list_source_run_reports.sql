/*
declaration:
  version: 0.1
  description: "list reports"
  method: get
  namespace: reports
  returns: json
  allowlist:
    query:
      - field: sorting
        type: string
        description: "sorting"
        enum: ["name desc", "name asc", "sector desc", "sector asc", "updatedAt asc", "updatedAt desc"]
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
        description: "base id of report"
      - field: agency_base_id
        type: string
        description: "agency base id"
      - field: agency_name
        type: string
        description: "name of agency"
      - field: url
        type: string
        description: "source url"
      - field: errors
        type: number
        description: "number of errors"
      - field: scraping_started_at
        type: timestamp
        description: "when scrapping started"
      - field: scraping_finished_at
        type: timestamp
        description: "when scrapping started"
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
WITH latest_reports AS (
    SELECT DISTINCT ON (base_id) 
        id, base_id, agency_base_id, agency_name, url, errors, 
        scraping_started_at, scraping_finished_at, is_deleted
    FROM monitoring.source_run_report 
    ORDER BY base_id, updated_at DESC
)
SELECT 
    id, base_id, agency_base_id, agency_name, url, errors, 
    scraping_started_at, scraping_finished_at,
    :page as page,
    CEIL(COUNT(*) OVER () / :page_size::DECIMAL) AS total_pages,
    (COUNT(*) OVER ()) AS total
FROM latest_reports
WHERE is_deleted = FALSE
ORDER BY 
    CASE WHEN :sorting = 'agency_name asc' THEN agency_name END ASC,
    CASE WHEN :sorting = 'agency_name desc' THEN agency_name END DESC,
    CASE WHEN :sorting = 'url asc' THEN url END ASC,
    CASE WHEN :sorting = 'url desc' THEN url END DESC,
    CASE WHEN :sorting = 'errors asc' THEN errors END ASC,
    CASE WHEN :sorting = 'errors desc' THEN errors END DESC,
    CASE WHEN :sorting = 'scraping_started_at asc' THEN scraping_started_at END ASC,
    CASE WHEN :sorting = 'scraping_started_at desc' THEN scraping_started_at END DESC,
    CASE WHEN :sorting = 'scraping_finished_at asc' THEN scraping_finished_at END ASC,
    CASE WHEN :sorting = 'scraping_finished_at desc' THEN scraping_finished_at END DESC,
    scraping_started_at DESC NULLS LAST
LIMIT :page_size::INTEGER 
OFFSET ((GREATEST(:page::INTEGER, 1) - 1) * :page_size::INTEGER);