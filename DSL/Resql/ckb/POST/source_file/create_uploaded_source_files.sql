INSERT INTO source_file (
    source_base_id, agency_base_id, base_id, file_name, subsector, original_data_url, type
)
SELECT
    :source_id::UUID,
    :agency_id::UUID,
    file_data.base_id::UUID,
    file_data.file_name,
    file_data.subsector,
    file_data.original_data_url,
    'uploaded_file'::source_file_type
FROM (
    SELECT
        (SELECT value) ->> 'base_id' AS base_id,
        (SELECT value) ->> 'file_name' AS file_name,
        (SELECT value) ->> 'subsector' AS subsector,
        (SELECT value) ->> 'original_data_url' AS original_data_url
    FROM JSON_ARRAY_ELEMENTS(ARRAY_TO_JSON(ARRAY[:files])) WITH ORDINALITY
) AS file_data
RETURNING NULL as url,  base_id as id, '' as hash, original_data_url, original_data_url as path;