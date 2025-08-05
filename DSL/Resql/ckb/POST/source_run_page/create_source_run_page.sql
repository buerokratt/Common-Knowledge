INSERT INTO source_run_page (
    agency_base_id, source_base_id, source_run_report_base_id, url, scraped_at, 
    error_type, error_message
)
VALUES (
    :agency_base_id::UUID, :source_base_id::UUID, :source_run_report_base_id::UUID,
    :url, :scraped_at::TIMESTAMP WITH TIME ZONE, :error_type, :error_message
)