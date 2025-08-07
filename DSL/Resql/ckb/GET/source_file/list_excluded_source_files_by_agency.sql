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
SELECT
    id, 
    base_id, 
    agency_base_id, 
    source_base_id
FROM source_file
WHERE 
    agency_base_id = :agency_base_id::UUID 
    AND is_excluded = true 
    AND is_deleted = false;