/*
declaration:
  version: 0.1
  description: "Get the latest original_data_url for a source_file by base_id"
  method: get
  namespace: source_file
  returns: json
  allowlist:
    query:
      - field: base_id
        type: string
        description: "Base identifier of the source file"
  response:
    fields:
      - field: base_id
        type: string
        description: "Base identifier of the source file"
      - field: original_data_url
        type: string
        description: "Original URL of the data file"
*/
SELECT base_id, original_data_url
FROM data_collection.source_file
WHERE updated_at = (
    SELECT max(updated_at)
    FROM data_collection.source_file
    WHERE base_id = :base_id::UUID
) AND is_deleted = FALSE;