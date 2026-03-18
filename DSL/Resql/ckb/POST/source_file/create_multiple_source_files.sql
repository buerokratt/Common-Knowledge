WITH request_payload AS (
    SELECT
    CAST(:source_base_id AS UUID) AS source_base_id,
    CAST(:agency_base_id AS UUID) AS agency_base_id,
        CAST(:urls AS JSONB) AS urls
)
INSERT INTO data_collection.source_file (
    base_id,
    source_base_id,
    agency_base_id,
    url,
    type,
    status,
    created_at,
    updated_at
)
SELECT
    gen_random_uuid(),
    request_payload.source_base_id,
    request_payload.agency_base_id,
    url_item->>'url',
    'scraped_file',
    'scraping',
    NOW(),
    NOW()
FROM request_payload
CROSS JOIN LATERAL jsonb_array_elements(request_payload.urls) AS url_item
RETURNING
    base_id AS id,
    url,
    COALESCE(original_data_hash, '') AS hash,
    source_base_id,
    status;
