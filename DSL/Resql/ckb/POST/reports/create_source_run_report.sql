INSERT INTO source_run_report (
    agency_base_id, source_base_id, agency_name, url, scraping_started_at, 
    scraping_finished_at, errors, scraping_log_url, cleaning_log_url
)
VALUES (
    :agency_base_id::UUID, :source_base_id::UUID, :agency_name, :url, :scraping_started_at::TIMESTAMP WITH TIME ZONE,
    NULL, 0, NULL, NULL
)
RETURNING id, base_id, agency_base_id, source_base_id, agency_name, url, 
          scraping_started_at, scraping_finished_at, errors, scraping_log_url, cleaning_log_url;
