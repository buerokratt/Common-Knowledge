/*
declaration:
  version: 0.1
  description: "Mark multiple source_file records as deleted by base_ids"
  method: post
  accepts: json
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: base_ids
        type: array
        description: "Array of source file base IDs to mark as deleted"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
      - field: base_id
        type: string
        description: "Source file base ID"
      - field: source_base_id
        type: string
        description: "Source base ID"
      - field: agency_base_id
        type: string
        description: "Agency base ID"
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
)
SELECT 
    copy_row_with_modifications(
        'data_collection.source_file',
        'id', '::UUID', latest.id::VARCHAR,
        ARRAY[
            'is_deleted', '::BOOLEAN', 'TRUE',
            'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
        ]::VARCHAR[]
    ) as id,
    latest.base_id,
    latest.source_base_id,
    latest.agency_base_id
FROM latest;
