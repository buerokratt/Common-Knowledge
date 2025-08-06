COPY (
    SELECT *
    FROM source
    WHERE (base_id, updated_at) NOT IN (
        SELECT base_id, max(updated_at)
        FROM source
        GROUP BY base_id
    ) AND updated_at < %(export_boundary)s
) TO stdout WITH csv HEADER;
