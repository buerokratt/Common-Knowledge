WITH latest_sources AS (
    SELECT DISTINCT ON (base_id) 
        id, base_id, agency_base_id, url, subsector, status, last_scraped_at, type, is_deleted
    FROM source
    WHERE agency_base_id = :agency_base_id::UUID
    ORDER BY base_id, updated_at DESC
)
SELECT 
    id, base_id, agency_base_id, url, subsector, status, last_scraped_at, type,
    :page as page,
    CEIL(COUNT(*) OVER () / :page_size::DECIMAL) AS total_pages,
    (COUNT(*) OVER ()) AS total
FROM latest_sources
WHERE is_deleted = FALSE
ORDER BY 
    CASE WHEN :sorting = 'url asc' THEN url END ASC,
    CASE WHEN :sorting = 'url desc' THEN url END DESC,
    CASE WHEN :sorting = 'subsector asc' THEN subsector END ASC,
    CASE WHEN :sorting = 'subsector desc' THEN subsector END DESC,
    CASE WHEN :sorting = 'last_scraped_at asc' THEN last_scraped_at END ASC,
    CASE WHEN :sorting = 'last_scraped_at desc' THEN last_scraped_at END DESC,
    CASE WHEN :sorting = 'status asc' THEN status END ASC,
    CASE WHEN :sorting = 'status desc' THEN status END DESC,
    last_scraped_at DESC NULLS LAST
LIMIT :page_size::INTEGER 
OFFSET ((GREATEST(:page::INTEGER, 1) - 1) * :page_size::INTEGER);