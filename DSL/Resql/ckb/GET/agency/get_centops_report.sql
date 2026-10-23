/*
declaration:
  version: 0.1
  description: "Get what was last sent to CentOps for an agency"
  method: get
  namespace: agency
  returns: json
  allowlist:
    query:
      - field: agency_base_id
        type: string
        description: "Agency base ID"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
      - field: agency_base_id
        type: string
        description: "Agency base ID"
      - field: reported_data_hash
        type: string
        description: "data_hash last sent to CentOps"
      - field: presigned_url_expires_at
        type: timestamp
        description: "When the link last sent expires"
      - field: reported_at
        type: timestamp
        description: "When it was sent"
*/
SELECT
    id, agency_base_id, reported_data_hash, presigned_url_expires_at, reported_at
FROM agency_management.centops_report
WHERE agency_base_id = :agency_base_id::UUID;
