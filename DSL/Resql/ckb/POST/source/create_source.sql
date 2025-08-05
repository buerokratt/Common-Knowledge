INSERT INTO source (
    agency_base_id, url, subsector, type, status
)
VALUES (
    :agency_base_id::UUID, 
    :url,
    :subsector,
    :type::source_type,
    'running'::source_status_type
)
RETURNING id, base_id, agency_base_id, url, subsector, type, status;
