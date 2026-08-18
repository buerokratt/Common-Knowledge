/*
declaration:
  version: 0.1
  description: "List latest sources by agency_base_id with pagination and sorting"
  method: get
  namespace: source
  returns: json
  allowlist:
    query:
      - field: agency_base_id
        type: string
        description: "Base ID of the associated agency"
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
          'subsector asc', 'subsector desc',
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
      - field: subsector
        type: string
        description: "Subsector classification"
      - field: status
        type: string
        description: "Status of the source"
      - field: last_scraped_at
        type: timestamp
        description: "Timestamp of the last scraping operation"
      - field: type
        type: string
        enum: ['url_to_scrape', 'file', 'api']
        description: "Type of the source"
      - field: page
        type: integer
        description: "Current page number"
      - field: total_pages
        type: integer
        description: "Total number of pages"
      - field: total
        type: integer
        description: "Total number of matching records"
      - field: has_finished_files
        type: boolean
        description: "True if source has at least one finished file"
      - field: is_stopping
        type: boolean
        description: "True if a stop was requested and is still being processed"
*/
WITH latest_sources AS (
    SELECT DISTINCT ON (base_id)
        id, base_id, agency_base_id, url, subsector, status, last_scraped_at, type, is_deleted, updated_at, is_stopping
    FROM data_collection.source
    WHERE agency_base_id = :agency_base_id::UUID
    ORDER BY base_id, updated_at DESC
)
SELECT
    ls.id, ls.base_id, ls.agency_base_id, ls.url, ls.subsector, ls.status, ls.last_scraped_at, ls.type, ls.is_stopping,
    :page as page,
    CEIL(COUNT(*) OVER () / :page_size::DECIMAL) AS total_pages,
    (COUNT(*) OVER ()) AS total,
    EXISTS (
        SELECT 1
        FROM data_collection.source_file sf
        WHERE sf.source_base_id = ls.base_id
          AND sf.is_deleted = FALSE
          AND sf.is_excluded = FALSE
          AND (sf.type = 'scraped_file' OR sf.type = 'api_file')
          AND sf.status = 'finished'::SOURCE_FILE_STATUS_TYPE
    ) as has_finished_files
FROM latest_sources ls
WHERE is_deleted = FALSE
ORDER BY
    CASE WHEN :sorting = 'url asc' THEN url END ASC,
    CASE WHEN :sorting = 'url desc' THEN url END DESC,
    CASE WHEN :sorting = 'subsector asc' THEN subsector END ASC,
    CASE WHEN :sorting = 'subsector desc' THEN subsector END DESC,
    CASE WHEN :sorting = 'last_scraped_at asc' THEN last_scraped_at END ASC NULLS LAST,
    CASE WHEN :sorting = 'last_scraped_at desc' THEN last_scraped_at END DESC NULLS LAST,
    CASE WHEN :sorting = 'status asc' THEN status END ASC,
    CASE WHEN :sorting = 'status desc' THEN status END DESC,
    updated_at DESC NULLS LAST
LIMIT :page_size::INTEGER 
OFFSET ((GREATEST(:page::INTEGER, 1) - 1) * :page_size::INTEGER);