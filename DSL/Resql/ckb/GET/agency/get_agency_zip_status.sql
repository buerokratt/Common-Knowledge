/*
declaration:
  version: 0.1
  description: "Get the latest data_hash, zip path and zipping status of an agency"
  method: get
  namespace: agency
  returns: json
  allowlist:
    query:
      - field: base_id
        type: string
        description: "Agency base ID"
  response:
    fields:
      - field: data_hash
        type: string
        description: "Checksum of the latest zip"
      - field: zipped_data_url
        type: string
        description: "Path of the latest zip"
      - field: is_zipping
        type: boolean
        description: "Whether a zip is being built now"
*/
SELECT
    data_hash, zipped_data_url, is_zipping
FROM agency_management.agency
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at)
      FROM agency_management.agency
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE LIMIT 1;
