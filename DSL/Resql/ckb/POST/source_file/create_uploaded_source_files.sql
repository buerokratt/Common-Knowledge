/*
declaration:
  version: 0.1
  description: "Insert multiple source_file records from JSON array of files"
  method: post
  accepts: json
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: source_id
        type: string
        description: "Source base ID"
      - field: agency_id
        type: string
        description: "Agency base ID"
      - field: files
        type: array
        description: "Array of file objects with base_id, file_name, subsector, original_data_url, file_size, uploaded_by"
  response:
    fields:
      - field: url
        type: string
        description: "URL (null)"
      - field: id
        type: string
        description: "Base ID of inserted file"
      - field: hash
        type: string
        description: "Empty hash"
      - field: original_data_url
        type: string
        description: "Original data URL"
      - field: path
        type: string
        description: "Path (same as original_data_url)"
*/
INSERT INTO data_collection.source_file (
    source_base_id, agency_base_id, base_id, file_name, subsector, original_data_url, file_size, uploaded_by, type
)
SELECT
    :source_id::UUID,
    :agency_id::UUID,
    file_data.base_id::UUID,
    file_data.file_name,
    file_data.subsector,
    file_data.original_data_url,
    file_data.file_size::BIGINT,
    file_data.uploaded_by,
    'uploaded_file'::source_file_type
FROM (
    SELECT
        (SELECT value) ->> 'base_id' AS base_id,
        (SELECT value) ->> 'file_name' AS file_name,
        (SELECT value) ->> 'subsector' AS subsector,
        (SELECT value) ->> 'original_data_url' AS original_data_url,
        (SELECT value) ->> 'file_size' AS file_size,
        (SELECT value) ->> 'uploaded_by' AS uploaded_by
    FROM JSON_ARRAY_ELEMENTS(ARRAY_TO_JSON(ARRAY[:files])) WITH ORDINALITY
) AS file_data
RETURNING NULL as url,  base_id as id, '' as hash, original_data_url, original_data_url as path;