/*
declaration:
  version: 0.1
  description: "take one scheduled to run source and mark it as running"
  method: post
  returns: json
  namespace: source
  response:
    fields
      - field: base_id
        type: string
        description: "base_id of record to run"
      - field: agency_base_id
        type: string
        description: "agency base id"
      - field: url
        type: string
        description: "url of the source"
*/
SELECT copy_row_with_modifications(
    'data_collection.source',
       'id', '::UUID', id::VARCHAR,
       ARRAY[
           'last_scrapping_at', '::TIMESTAMP WITH TIME ZONE', next_scrapping_at::VARCHAR,
           'next_scrapping_at', '::TIMESTAMP WITH TIME ZONE', NULL,
           'status', '::SOURCE_STATUS_TYPE', 'running',
           'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
       ]::VARCHAR[]
), base_id, agency_base_id, type, url
FROM data_collection.source
WHERE (base_id, updated_at) IN (
    SELECT base_id, max(updated_at) FROM data_collection.source
    GROUP BY base_id
)
    AND is_deleted = FALSE
    AND (
        status = 'new'
        OR (
            update_automatically = TRUE
            AND status NOT IN ('running', 'failed', 'in_review')
            AND next_scrapping_at <= NOW()
        )
    )
LIMIT 1;
