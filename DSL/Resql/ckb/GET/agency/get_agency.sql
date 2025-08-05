/*
declaration:
  version: 0.1
  description: "get agency by base id"
  method: get
  namespace: agency
  returns: json
  allowlist:
    query:
      - field: base_id
        type: string
        description: "agency base id"
  response:
    fields:
      - field: id
        type: integer
        description: "Primary key of the agency entry"
      - field: base_id
        type: string
        description: "base id of agency"
      - field: name
        type: string
        description: "name of agency"
      - field: sector
        type: string
        description: "sector"
      - field: external_id
        type: timestamp
        description: "external id"
      - field: updated_at
        type: timestamp
        description: "when was updated last time"
*/
SELECT
    id, base_id, name, sector, external_id, updated_at
FROM agency 
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM agency 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE LIMIT 1;