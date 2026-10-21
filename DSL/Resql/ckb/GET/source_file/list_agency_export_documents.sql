/*
declaration:
  version: 0.1
  description: "Keep deleted, excluded and unfinished documents so exports retain removal and hold signals"
  method: get
  namespace: source_file
  returns: json
  allowlist:
    query:
      - field: agency_base_id
        type: string
        description: "UUID of the agency base"
      - field: after_base_id
        type: string
        description: "Exclusive lower bound for the document base ID"
      - field: page_size
        type: number
        description: "Maximum documents per page"
  response:
    fields:
      - field: base_id
        type: string
        description: "Base ID of the source file"
      - field: source_base_id
        type: string
        description: "Base ID of the source"
      - field: url
        type: string
        description: "Source file URL"
      - field: page_title
        type: string
        description: "Page title"
      - field: subsector
        type: string
        description: "Subsector of the source file"
      - field: status
        type: string
        description: "Processing status"
      - field: cleaned_data_url
        type: string
        description: "Cleaned data URL"
      - field: cleaned_metadata_url
        type: string
        description: "Cleaned metadata URL"
      - field: edited_data_url
        type: string
        description: "Edited data URL"
      - field: edited_metadata_url
        type: string
        description: "Edited metadata URL"
      - field: file_size
        type: integer
        description: "File size in bytes"
      - field: is_excluded
        type: boolean
        description: "Whether the file is excluded"
      - field: is_deleted
        type: boolean
        description: "Whether the file is deleted"
      - field: updated_at
        type: timestamp
        description: "Timestamp of the latest version"
*/
SELECT DISTINCT ON (base_id)
    base_id,
    source_base_id,
    url,
    page_title,
    subsector,
    status,
    cleaned_data_url,
    cleaned_metadata_url,
    edited_data_url,
    edited_metadata_url,
    file_size,
    is_excluded,
    is_deleted,
    updated_at
FROM data_collection.source_file
WHERE agency_base_id = :agency_base_id::UUID
  AND base_id > :after_base_id::UUID
ORDER BY base_id, updated_at DESC
LIMIT :page_size::INTEGER;
