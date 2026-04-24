/*
declaration:
  version: 0.1
  description: "list agency with data hash and url"
  method: get
  namespace: agency
  allowlist: {}
  returns: json
  response:
    fields:
      - field: agency_data_hash
        type: string
        description: "data hash of the agency"
      - field: path
        type: string
        description: "storage path for zipped data"

*/
SELECT
    data_hash AS agency_data_hash,
    zipped_data_url AS path
FROM agency_management.agency
WHERE is_deleted = FALSE
  AND updated_at = (
      SELECT MAX(updated_at)
      FROM agency_management.agency
      WHERE is_deleted = FALSE
  );