/*
declaration:
  version: 0.1
  description: "Check if a non-deleted source_file already exists for a source by URL"
  method: get
  namespace: source_file
  returns: json
  allowlist:
    query:
      - field: source_base_id
        type: string
        description: "Base ID of the source"
      - field: url
        type: string
        description: "URL to check"
  response:
    fields:
      - field: exists
        type: boolean
        description: "Whether a matching source_file exists"
*/
SELECT count(*) > 0 AS exists
FROM data_collection.source_file
WHERE (base_id, updated_at) IN (
    SELECT base_id, max(updated_at)
    FROM data_collection.source_file
    WHERE source_base_id = :source_base_id::UUID
    GROUP BY base_id
) AND source_base_id = :source_base_id::UUID
    AND is_deleted = FALSE
    AND url = :url;
