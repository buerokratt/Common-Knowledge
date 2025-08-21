/*
declaration:
  version: 0.1
  description: "Get source_file by source id and mark it as running"
  method: post
  returns: json
  namespace: scheduler
  allowlist:
    body:
      - field: source_base_id
        type: string
        description: "base id of source"
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
    base_id as id, url, original_data_hash as hash, external_id
FROM data_collection.source_file
WHERE (base_id, updated_at) IN (
        SELECT base_id, max(updated_at)
        FROM data_collection.source_file
        WHERE source_base_id = :source_base_id::UUID
        GROUP BY base_id
    )
    AND is_excluded = FALSE
    AND is_deleted = FALSE
    AND (type = 'scraped_file' OR type = 'api_file')
    AND status = 'finished'::SOURCE_FILE_STATUS_TYPE
    AND last_scraped_at < :reference_time::TIMESTAMP WITH TIME ZONE
LIMIT 1;
