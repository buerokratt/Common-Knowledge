/*
declaration:
  version: 0.1
  description: "Get source_file by source file id and mark it as running"
  method: post
  returns: json
  namespace: scheduler
  allowlist:
    body:
      - field: base_id
        type: string
        description: "base id of source file"
      - field: reference_time
        type: string
        description: "fetch source files, scrapped before reference time"
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
SELECT
    copy_row_with_modifications(
        'data_collection.source_file',
        'id', '::UUID', id::VARCHAR,
        ARRAY[
            'status', '::SOURCE_FILE_STATUS_TYPE', 'scraping',
            'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
        ]::VARCHAR[]
    ),
    base_id as id, url, original_data_hash as hash, source_base_id, agency_base_id, type, external_id
FROM data_collection.source_file
WHERE base_id = :base_id::UUID AND
    updated_at = (
        SELECT max(updated_at)
        FROM data_collection.source_file
        WHERE base_id = :base_id::UUID
    )
    AND is_deleted = FALSE
    AND (type = 'scraped_file' OR type = 'api_file')
LIMIT 1;
