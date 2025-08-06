COPY (
    SELECT *
    FROM source_run_page
    WHERE (base_id, updated_at) NOT IN (
        SELECT base_id, max(updated_at)
        FROM source_run_page
        GROUP BY base_id
    ) AND updated_at < %(export_boundary)s
) TO stdout WITH csv HEADER;
