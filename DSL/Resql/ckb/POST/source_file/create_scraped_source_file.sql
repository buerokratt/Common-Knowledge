/*
declaration:
  version: 0.1
  description: "Insert a new source_file record with status 'cleaning'"
  method: post
  accepts: json
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: source_base_id
        type: string
        description: "Source base ID"
      - field: agency_base_id
        type: string
        description: "Agency base ID"
      - field: url
        type: string
        description: "URL of the source file"
      - field: page_title
        type: string
        description: "Title of the page"
      - field: scraped_at
        type: string
        description: "Timestamp when scraped"
      - field: original_data_hash
        type: string
        description: "Original data hash"
      - field: type
        type: string
        description: "Type of source file"
      - field: external_id
        type: string
        description: "External identifier"
  response:
    fields:
      - field: base_id
        type: string
        description: "Base ID of the inserted source file"
*/
INSERT INTO data_collection.source_file (
    source_base_id, agency_base_id, url, page_title,
    last_scraped_at, originally_scraped, original_data_hash, type, status, external_id
)
VALUES (
    :source_base_id::UUID, :agency_base_id::UUID, :url, :page_title,
    :scraped_at::TIMESTAMP WITH TIME ZONE, :scraped_at::TIMESTAMP WITH TIME ZONE,
    :original_data_hash, :type::source_file_type, 'scraping'::source_file_status_type, :external_id
)
RETURNING base_id
