/*
declaration:
  version: 0.1
  description: "Get multiple source files by base IDs and mark them as running"
  method: post
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: base_ids
        type: array
        items:
          type: string
        description: "array of base ids of source files"
  response:
    fields:
      - field: id
        type: string
        description: "base id of source_file"
      - field: urls
        type: array
        items:
            type: string
        description: "urls to run"
      - field: hash
        type: string
        description: "current scraped data hash"
*/
WITH ids AS (
    SELECT json_array_elements_text(COALESCE(ARRAY_TO_JSON(ARRAY[:base_ids]), '[]'::json)) AS base_id_text
),
latest AS (
    SELECT sf.*
    FROM data_collection.source_file sf
    JOIN (
        SELECT base_id, MAX(updated_at) AS max_updated
        FROM data_collection.source_file
        GROUP BY base_id
    ) m ON sf.base_id = m.base_id AND sf.updated_at = m.max_updated
    JOIN ids ON sf.base_id::text = ids.base_id_text
    WHERE sf.is_deleted = FALSE
        AND (sf.type = 'scraped_file' OR sf.type = 'api_file')
)
SELECT 
    copy_row_with_modifications(
        'data_collection.source_file',
        'id', '::UUID', latest.id::VARCHAR,
        ARRAY[
            'status', '::SOURCE_FILE_STATUS_TYPE', 'scraping',
            'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
        ]::VARCHAR[]
    ) as copy_result,
    latest.base_id as id,
    latest.url,
    latest.original_data_hash as hash,
    latest.source_base_id,
    latest.agency_base_id,
    latest.type,
    latest.external_id
FROM latest;