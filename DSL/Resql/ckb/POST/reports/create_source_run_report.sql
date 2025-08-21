/*
declaration:
  version: 0.1
  description: "Insert a new source_run_report record with initial scraping state"
  method: post
  accepts: json
  returns: json
  namespace: source_run_report
  allowlist:
    body:
      - field: agency_base_id
        type: string
        description: "Agency base ID"
      - field: source_base_id
        type: string
        description: "Source base ID"
      - field: agency_name
        type: string
        description: "Agency name"
      - field: url
        type: string
        description: "URL"
      - field: scraping_started_at
        type: string
        description: "Scraping start timestamp"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
      - field: base_id
        type: string
        description: "Base ID"
      - field: agency_base_id
        type: string
        description: "Agency base ID"
      - field: source_base_id
        type: string
        description: "Source base ID"
      - field: agency_name
        type: string
        description: "Agency name"
      - field: url
        type: string
        description: "URL"
      - field: scraping_started_at
        type: string
        description: "Scraping start timestamp"
      - field: scraping_finished_at
        type: string
        description: "Scraping finish timestamp"
      - field: errors
        type: integer
        description: "Number of errors"
      - field: scraping_log_url
        type: string
        description: "Scraping log URL"
      - field: cleaning_log_url
        type: string
        description: "Cleaning log URL"
*/
INSERT INTO monitoring.source_run_report (
    agency_base_id, source_base_id, agency_name, url, scraping_started_at, 
    scraping_finished_at, errors, scraping_log_url, cleaning_log_url
)
VALUES (
    :agency_base_id::UUID, :source_base_id::UUID, :agency_name, :url, :scraping_started_at::TIMESTAMP WITH TIME ZONE,
    NULL, 0, NULL, NULL
)
RETURNING id, base_id, agency_base_id, source_base_id, agency_name, url, 
          scraping_started_at, scraping_finished_at, errors, scraping_log_url, cleaning_log_url;
