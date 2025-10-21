/*
declaration:
  version: 0.1
  description: "List latest API sources with pagination and sorting"
  method: get
  namespace: source
  returns: json
  allowlist:
    query:
      - field: page
        type: integer
        description: "Current page number"
      - field: page_size
        type: integer
        description: "Number of results per page"
      - field: sorting
        type: string
        enum: [
          'url asc', 'url desc',
          'last_scraped_at asc', 'last_scraped_at desc',
          'status asc', 'status desc'
        ]
        description: "Sorting method for the result set"
  response:
    fields:
      - field: id
        type: string
        description: "Primary key of the source entry"
      - field: base_id
        type: string
        description: "Base identifier for the source"
      - field: agency_base_id
        type: string
        description: "Base ID of the associated agency"
      - field: url
        type: string
        description: "URL of the source"
      - field: status
        type: string
        description: "Status of the source"
      - field: last_scraped_at
        type: timestamp
        description: "Timestamp of the last scraping operation"
      - field: page
        type: integer
        description: "Current page number"
      - field: total_pages
        type: integer
        description: "Total number of pages"
      - field: total
        type: integer
        description: "Total number of matching records"
*/
WITH latest_sources AS (
    SELECT DISTINCT ON (base_id)
        id, base_id, agency_base_id, url, status, last_scraped_at, type, is_deleted, updated_at
    FROM data_collection.source
    WHERE type = 'api'::source_type
    ORDER BY base_id, updated_at DESC
)
SELECT
    id, base_id, agency_base_id, url, status, last_scraped_at,
    :page as page,
    CEIL(COUNT(*) OVER () / :page_size::DECIMAL) AS total_pages,
    (COUNT(*) OVER ()) AS total
FROM latest_sources
WHERE is_deleted = FALSE
ORDER BY
    CASE WHEN :sorting = 'url asc' THEN url END ASC,
    CASE WHEN :sorting = 'url desc' THEN url END DESC,
    CASE WHEN :sorting = 'last_scraped_at asc' THEN last_scraped_at END ASC,
    CASE WHEN :sorting = 'last_scraped_at desc' THEN last_scraped_at END DESC,
    CASE WHEN :sorting = 'status asc' THEN status END ASC,
    CASE WHEN :sorting = 'status desc' THEN status END DESC,
    updated_at DESC NULLS LAST
LIMIT :page_size::INTEGER 
OFFSET ((GREATEST(:page::INTEGER, 1) - 1) * :page_size::INTEGER);