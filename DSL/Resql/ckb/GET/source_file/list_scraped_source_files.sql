WITH latest_scraped_pages AS (
    SELECT DISTINCT ON (base_id) 
        id, base_id, source_base_id, url, page_title, status,
        original_data_url, cleaned_data_url, edited_data_url, external_id,
        is_excluded, updated_at, originally_scraped, last_scraped_at, is_deleted
    FROM source_file
    WHERE type = :type::source_file_type
      AND (:source_id IS NULL OR source_base_id = :source_id::UUID)
    ORDER BY base_id, updated_at DESC
)
SELECT 
    id, base_id, source_base_id, url, page_title, status, external_id,
    original_data_url, cleaned_data_url, edited_data_url, is_excluded, updated_at, originally_scraped, last_scraped_at,
    :page as page,
    CEIL(COUNT(*) OVER () / :page_size::DECIMAL) AS total_pages,
    (COUNT(*) OVER ()) AS total
FROM latest_scraped_pages
WHERE is_deleted = FALSE
ORDER BY 
    CASE WHEN :sorting = 'url asc' THEN url END ASC,
    CASE WHEN :sorting = 'url desc' THEN url END DESC,
    CASE WHEN :sorting = 'page_title asc' THEN page_title END ASC,
    CASE WHEN :sorting = 'page_title desc' THEN page_title END DESC,
    CASE WHEN :sorting = 'excluded asc' THEN is_excluded END ASC,
    CASE WHEN :sorting = 'excluded desc' THEN is_excluded END DESC,
    CASE WHEN :sorting = 'status asc' THEN status END ASC,
    CASE WHEN :sorting = 'status desc' THEN status END DESC,
    CASE WHEN :sorting = 'last_scraped_at asc' THEN last_scraped_at END ASC,
    CASE WHEN :sorting = 'last_scraped_at desc' THEN last_scraped_at END DESC,
    CASE WHEN :sorting = 'external_id asc' THEN external_id END ASC,
    CASE WHEN :sorting = 'external_id desc' THEN external_id END DESC,
    last_scraped_at DESC NULLS LAST
LIMIT :page_size::INTEGER 
OFFSET ((GREATEST(:page::INTEGER, 1) - 1) * :page_size::INTEGER);