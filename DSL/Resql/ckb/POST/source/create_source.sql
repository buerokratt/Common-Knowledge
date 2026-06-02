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
      - field: quality_control
        type: string
        description: "Quality control method (basic, comprehensive, or null)"
        required: false
      - field: extract_images
        type: boolean
        description: "Whether to extract images when cleaning"
        required: false
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
      - field: quality_control
        type: string
        enum: ['basic', 'comprehensive']
        description: "Quality control method"
        required: false
      - field: extract_images
        type: boolean
        description: "Whether to extract images when cleaning"
        required: false
*/
INSERT INTO data_collection.source (
    agency_base_id, url, subsector, type, status, quality_control, extract_images
)
VALUES (
    :agency_base_id::UUID, 
    :url,
    :subsector,
    :type::source_type,
    'running'::source_status_type,
  NULLIF(:quality_control, '')::quality_control_type,
  COALESCE(:extract_images::BOOLEAN, FALSE)
)
RETURNING id, base_id, agency_base_id, url, subsector, type, status, quality_control, extract_images;
