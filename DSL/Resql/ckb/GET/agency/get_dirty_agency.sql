SELECT COALESCE(
    (WITH latest_records AS (
        SELECT DISTINCT ON (base_id) base_id, zip_dirty, is_zipping
        FROM agency
        WHERE is_deleted = FALSE
        ORDER BY base_id, updated_at DESC
    )
    SELECT base_id::text
    FROM latest_records
    WHERE zip_dirty = TRUE AND is_zipping = FALSE
    LIMIT 1), 
    ''
) as base_id;