/*
declaration:
  version: 0.1
  description: "update scheduler next run timestamp"
  method: post
  returns: json
  namespace: source
  allowlist:
    body:
      - field: base_id
        type: string
        description: "base id for source to update"
      - field next_scrapping_at
        type: string
        description: "timestamp string of rendered next run timestamp"
  response: {}
*/
SELECT copy_row_with_modifications(
    'data_collection.source',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'next_scrapping_at', '::TIMESTAMP WITH TIME ZONE', :next_scrapping_at,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
    ]::VARCHAR[]
) as id
FROM data_collection.source
WHERE base_id = :base_id::UUID
    AND updated_at = (
        SELECT MAX(updated_at)
        FROM data_collection.source
        WHERE base_id = :base_id::UUID
    )
    AND is_deleted = FALSE
    AND update_automatically = TRUE
    AND next_scrapping_at IS NULL
    AND status NOT IN ('running', 'failed');
