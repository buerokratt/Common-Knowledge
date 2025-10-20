/*
declaration:
  version: 0.1
  description: "Check if file names already exist for a source (latest version, non-deleted)"
  method: post
  accepts: json
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: source_id
        type: string
        description: "Source base ID"
      - field: file_names
        type: string
        description: "Comma-separated file names to check"
  response:
    fields:
      - field: file_name
        type: string
        description: "Duplicate file name found"
*/
WITH latest_files AS (
    SELECT DISTINCT ON (base_id)
        base_id, file_name, is_deleted
    FROM data_collection.source_file
    WHERE source_base_id = :source_id::UUID
      AND type = 'uploaded_file'
    ORDER BY base_id, updated_at DESC
)
SELECT DISTINCT file_name
FROM latest_files
WHERE is_deleted = FALSE
  AND file_name = ANY(string_to_array(:fileNames, ','));
