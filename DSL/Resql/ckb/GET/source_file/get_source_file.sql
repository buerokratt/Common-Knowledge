SELECT base_id, original_data_url
FROM source_file
WHERE updated_at = (
    SELECT max(updated_at)
    FROM source_file
    WHERE base_id = :base_id::UUID
) AND is_deleted = FALSE;