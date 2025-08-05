INSERT INTO source_file (
    source_base_id, agency_base_id, url, page_title,
    last_scraped_at, originally_scraped, original_data_hash, type, status, external_id
)
VALUES (
    :source_base_id::UUID, :agency_base_id::UUID, :url, :page_title,
    :scraped_at::TIMESTAMP WITH TIME ZONE, :scraped_at::TIMESTAMP WITH TIME ZONE,
    :original_data_hash, :type::source_file_type, 'cleaning'::source_file_status_type, :external_id
)
RETURNING base_id
