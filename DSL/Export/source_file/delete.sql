DELETE FROM source_file
WHERE (base_id, updated_at) NOT IN (
    SELECT base_id, max(updated_at)
    FROM source_file
    GROUP BY base_id
) AND updated_at < %(export_boundary)s;
