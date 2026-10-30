/*
declaration:
  version: 0.1
  description: "Insert or update what was last sent to CentOps for an agency"
  method: post
  accepts: json
  returns: json
  namespace: agency
  allowlist:
    body:
      - field: agency_base_id
        type: string
        description: "Agency base ID"
      - field: reported_data_hash
        type: string
        description: "data_hash sent to CentOps"
      - field: presigned_url_expires_at
        type: string
        description: "expires_at of the link sent, from file-processing"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
      - field: agency_base_id
        type: string
        description: "Agency base ID"
*/
INSERT INTO agency_management.centops_report (
    agency_base_id, reported_data_hash, presigned_url_expires_at, reported_at
)
VALUES (
    :agency_base_id::UUID,
    :reported_data_hash,
    :presigned_url_expires_at::TIMESTAMP WITH TIME ZONE,
    NOW()
)
ON CONFLICT (agency_base_id) DO UPDATE SET
    reported_data_hash = EXCLUDED.reported_data_hash,
    presigned_url_expires_at = EXCLUDED.presigned_url_expires_at,
    reported_at = EXCLUDED.reported_at
RETURNING id, agency_base_id;
