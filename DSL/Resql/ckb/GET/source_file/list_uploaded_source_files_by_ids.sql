WITH latest_files AS (
    SELECT DISTINCT ON (base_id) 
        id, base_id, source_base_id, file_name, subsector, original_data_url, cleaned_data_url, 
        edited_data_url, is_excluded, created_at, updated_at, status, is_deleted
    FROM source_file 
    WHERE type = 'uploaded_file'
      AND base_id = ANY(string_to_array(:source_file_ids, ',')::UUID[])
    ORDER BY base_id, updated_at DESC
)
SELECT 
    id, base_id, source_base_id, file_name, subsector, original_data_url, cleaned_data_url, 
    edited_data_url, is_excluded, created_at, updated_at, status,
    :page as page,
    CEIL(:total_count::DECIMAL / :page_size::DECIMAL) AS total_pages,
    :total_count AS total
FROM latest_files
WHERE is_deleted = FALSE
ORDER BY 
    CASE WHEN :sorting = 'file_name asc' THEN file_name END ASC,
    CASE WHEN :sorting = 'file_name desc' THEN file_name END DESC,
    CASE WHEN :sorting = 'subsector asc' THEN subsector END ASC,
    CASE WHEN :sorting = 'subsector desc' THEN subsector END DESC,
    CASE WHEN :sorting = 'is_excluded asc' THEN is_excluded END ASC,
    CASE WHEN :sorting = 'is_excluded desc' THEN is_excluded END DESC,
    CASE WHEN :sorting = 'created_at asc' THEN created_at END ASC,
    CASE WHEN :sorting = 'created_at desc' THEN created_at END DESC,
    created_at DESC NULLS LAST;