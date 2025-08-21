/*
declaration:
  version: 0.1
  description: "Insert a new record into source_run_page"
  method: post
  accepts: json
  returns: json
  namespace: source_run_page
  allowlist:
    body:
      - field: agency_base_id
        type: string
        description: "Agency base ID"
      - field: source_base_id
        type: string
        description: "Source base ID"
      - field: source_run_report_base_id
        type: string
        description: "Source run report base ID"
      - field: url
        type: string
        description: "URL"
      - field: scraped_at
        type: string
        description: "Timestamp of scraping"
      - field: error_type
        type: string
        description: "Error type"
      - field: error_message
        type: string
        description: "Error message"
*/
INSERT INTO monitoring.source_run_page (
    agency_base_id, source_base_id, source_run_report_base_id, url, scraped_at,
    error_type, error_message
)
VALUES (
    :agency_base_id::UUID, :source_base_id::UUID, :source_run_report_base_id::UUID,
    :url, :scraped_at::TIMESTAMP WITH TIME ZONE, :error_type, :error_message
);
