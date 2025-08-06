ChatGPT said:
sql
Copy
Edit
/*
declaration:
  version: 0.1
  description: "Insert a new source record with status set to 'running'"
  method: post
  accepts: json
  returns: json
  namespace: source
  allowlist:
    body:
      - field: agency_base_id
        type: string
        description: "Agency base ID"
      - field: url
        type: string
        description: "Source URL"
      - field: subsector
        type: string
        description: "Subsector"
      - field: type
        type: string
        description: "Source type"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
      - field: base_id
        type: string
        description: "Base ID"
      - field: agency_base_id
        type: string
        description: "Agency base ID"
      - field: url
        type: string
        description: "Source URL"
      - field: subsector
        type: string
        description: "Subsector"
      - field: type
        type: string
        description: "Source type"
      - field: status
        type: string
        enum: ['new', 'running', 'finished', 'failed']
        description: "Source status"
*/
INSERT INTO source (
    agency_base_id, url, subsector, type, status
)
VALUES (
    :agency_base_id::UUID, 
    :url,
    :subsector,
    :type::source_type,
    'running'::source_status_type
)
RETURNING id, base_id, agency_base_id, url, subsector, type, status;
