/*
declaration:
  version: 0.1
  description: "list agency with data availability"
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
      - field: is_data_available
        type: integer
        description: "is data available"
      - field: client_id
        type: string
        description: "base id of agency"

*/
SELECT
    base_id::TEXT AS client_id,
    CASE 
        WHEN zipped_data_url IS NOT NULL AND zipped_data_url != '' 
        THEN TRUE 
        ELSE FALSE 
    END AS is_data_available
FROM agency_management.agency a1
WHERE base_id::TEXT = ANY(STRING_TO_ARRAY(:agencyIds, ','))
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM agency_management.agency a2
      WHERE a2.base_id = a1.base_id
        AND a2.is_deleted = FALSE
  )
  AND is_deleted = FALSE;