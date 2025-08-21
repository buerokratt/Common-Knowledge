/*
declaration:
  version: 0.1
  description: "Insert a new agency record"
  method: post
  accepts: json
  returns: json
  namespace: agency
  allowlist:
    body:
      - field: name
        type: string
        description: "Agency name"
      - field: sector
        type: string
        description: "Agency sector"
      - field: external_id
        type: string
        description: "External identifier"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
      - field: base_id
        type: string
        description: "Base ID"
      - field: name
        type: string
        description: "Agency name"
      - field: sector
        type: string
        description: "Agency sector"
      - field: external_id
        type: string
        description: "External identifier"
      - field: created_at
        type: string
        description: "Record creation timestamp"
      - field: updated_at
        type: string
        description: "Record last update timestamp"
*/
INSERT INTO agency_management.agency (name, sector, external_id)
VALUES (:name, :sector, :external_id)
RETURNING id, base_id, name, sector, external_id, created_at, updated_at;
