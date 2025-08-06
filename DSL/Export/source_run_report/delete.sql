DELETE FROM source_run_report
WHERE (base_id, updated_at) NOT IN (
    SELECT base_id, max(updated_at)
    FROM source_run_report
    GROUP BY base_id
) AND updated_at < %(export_boundary)s;
