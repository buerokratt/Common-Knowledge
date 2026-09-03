/*
declaration:
  version: 0.1
  description: "Get latest source files by source base ID and mark them as scraping"
  method: post
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: source_base_id
        type: string
        description: "base id of source"
  response:
    fields:
      - field: id
        type: string
        description: "base id of source_file"
      - field: url
        type: string
        description: "url to run"
      - field: hash
        type: string
        description: "current scraped data hash"
*/
WITH latest AS (
    SELECT sf.*
    FROM data_collection.source_file sf
    JOIN (
        SELECT base_id, MAX(updated_at) AS max_updated
        FROM data_collection.source_file
        GROUP BY base_id
    ) m ON sf.base_id = m.base_id AND sf.updated_at = m.max_updated
    WHERE sf.source_base_id = :source_base_id::UUID
        AND sf.is_excluded = FALSE
        AND sf.is_deleted = FALSE
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
    latest.source_base_id as "sourceBaseId",
    latest.agency_base_id as "agencyBaseId",
    latest.type,
    latest.external_id
FROM latest;
