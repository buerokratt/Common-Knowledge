/*
declaration:
  version: 0.1
  description: "list agency with data hash"
  method: get
  namespace: agency
  returns: json
  allowlist:
    query:
      - field: agencyIds
        type: string
        description: "agency base ids, comma separated"
  response:
    fields:
      - field: data_hash
        type: integer
        description: "data hash"
      - field: client_id
        type: string
        description: "base id of agency"

*/
SELECT
    base_id::TEXT AS client_id,
    data_hash AS client_data_hash
FROM agency_management.agency a1
WHERE base_id::TEXT = ANY(STRING_TO_ARRAY(:agencyIds, ','))
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM agency_management.agency a2
      WHERE a2.base_id = a1.base_id
        AND a2.is_deleted = FALSE
  )
  AND is_deleted = FALSE;