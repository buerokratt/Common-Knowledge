/*
declaration:
  version: 0.1
  description: "Update status and related timestamps of the latest source record by base_id"
  method: post
  accepts: json
  returns: json
  namespace: source
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Source base ID"
      - field: status
        type: string
        description: "New source status"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
*/
SELECT copy_row_with_modifications(
    'source',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'status', '::source_status_type', :status,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
        'is_stopping', '::BOOLEAN', FALSE::VARCHAR,
        'last_scraped_at', '::TIMESTAMP WITH TIME ZONE', CASE
            WHEN :status::source_status_type = 'finished'
                THEN NOW()::VARCHAR
            ELSE
                last_scraped_at::VARCHAR
        END,
        'next_scrapping_at', '::TIMESTAMP WITH TIME ZONE', NULL
    ]::VARCHAR[]
) as id
FROM source
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM source 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;