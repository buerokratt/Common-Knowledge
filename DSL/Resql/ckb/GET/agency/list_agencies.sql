/*
declaration:
  version: 0.1
  description: "list agencies"
  method: get
  namespace: agency
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
        description: "Primary key of the agency entry"
      - field: base_id
        type: string
        description: "base id of agency"
      - field: name
        type: string
        description: "name of agency"
      - field: sector
        type: string
        description: "sector"
      - field: updated_at
        type: timestamp
        description: "when was updated last time"
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
WITH latest_agencies AS (
    SELECT DISTINCT ON (base_id) 
        id, base_id, name, sector, type, is_deleted, updated_at
    FROM agency_management.agency 
    ORDER BY base_id, updated_at DESC
)
SELECT 
    id, base_id, name, sector, updated_at,
    :page as page,
    CEIL(COUNT(*) OVER () / :page_size::DECIMAL) AS total_pages,
    (COUNT(*) OVER ()) AS total
FROM latest_agencies
WHERE is_deleted = FALSE AND type <> 'api'
ORDER BY 
    CASE WHEN :sorting = 'name asc' THEN name END ASC,
    CASE WHEN :sorting = 'name desc' THEN name END DESC,
    CASE WHEN :sorting = 'sector asc' THEN sector END ASC,
    CASE WHEN :sorting = 'sector desc' THEN sector END DESC,
    CASE WHEN :sorting = 'updatedAt asc' THEN updated_at END ASC,
    CASE WHEN :sorting = 'updatedAt desc' THEN updated_at END DESC,
    updated_at DESC
LIMIT :page_size::INTEGER 
OFFSET ((GREATEST(:page::INTEGER, 1) - 1) * :page_size::INTEGER);
