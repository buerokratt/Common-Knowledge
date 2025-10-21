/*
declaration:
  version: 0.1
  description: "Get non-deleted, excluded source files by agency"
  method: get
  namespace: source_file
  returns: json
  allowlist:
    query:
      - field: agency_base_id
        type: string
        description: "UUID of the agency base"
  response:
    fields:
      - field: id
        type: integer
        description: "Primary key of the source file"
      - field: base_id
        type: string
        description: "Base ID of the source file"
      - field: agency_base_id
        type: string
        description: "Base ID of the agency"
      - field: source_base_id
        type: string
        description: "Base ID of the source"
*/
WITH latest_files AS (
    SELECT DISTINCT ON (base_id)
        id, base_id, agency_base_id, source_base_id, is_deleted, updated_at
    FROM data_collection.source_file
    WHERE agency_base_id = :agency_base_id::UUID
      AND is_excluded = true
    ORDER BY base_id, updated_at DESC
)
SELECT
    id,
    base_id,
    agency_base_id,
    source_base_id
FROM latest_files
WHERE is_deleted = false
ORDER BY updated_at DESC NULLS LAST;