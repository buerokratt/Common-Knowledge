/*
declaration:
  version: 0.1
  description: "List all in_review, non-excluded, non-deleted source files for a source"
  method: get
  namespace: source_file
  returns: json
  allowlist:
    query:
      - field: source_id
        type: string
        description: "Filter by source base ID (UUID), required"
  response:
    fields:
      - field: id
        type: integer
      - field: base_id
        type: string
      - field: source_base_id
        type: string
      - field: url
        type: string
      - field: page_title
        type: string
      - field: status
        type: string
      - field: original_data_url
        type: string
      - field: original_metadata_url
        type: string
      - field: is_excluded
        type: boolean
      - field: is_deleted
        type: boolean
      - field: updated_at
        type: timestamp
*/
SELECT id, base_id, source_base_id, url, page_title, status, original_data_url, original_metadata_url, is_excluded, is_deleted, updated_at
FROM data_collection.source_file
WHERE source_base_id = :source_id::UUID
  AND is_excluded = FALSE
  AND is_deleted = FALSE
  AND status = 'in_review';