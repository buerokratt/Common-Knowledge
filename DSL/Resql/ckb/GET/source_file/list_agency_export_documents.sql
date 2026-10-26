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
      - field: type
        type: string
        description: "scraped_file, uploaded_file or api_file"
      - field: source_is_deleted
        type: boolean
        description: "Whether the latest version of the file's source is deleted"
      - field: updated_at
        type: timestamp
        description: "Timestamp of the latest version"
*/
-- Deleting a source marks only the source row, never its files, so each file
-- carries its source's latest is_deleted as a separate deletion signal. A file
-- whose source row is missing is not treated as deleted.
WITH page AS (
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
        type,
        updated_at
    FROM data_collection.source_file
    WHERE agency_base_id = :agency_base_id::UUID
      AND base_id > :after_base_id::UUID
    ORDER BY base_id, updated_at DESC
    LIMIT :page_size::INTEGER
)

SELECT
    p.base_id,
    p.source_base_id,
    p.url,
    p.page_title,
    p.subsector,
    p.status,
    p.cleaned_data_url,
    p.cleaned_metadata_url,
    p.edited_data_url,
    p.edited_metadata_url,
    p.file_size,
    p.is_excluded,
    p.is_deleted,
    p.type,
    COALESCE(ls.is_deleted, FALSE) AS source_is_deleted,
    p.updated_at
FROM page AS p
LEFT JOIN LATERAL (
    SELECT s.is_deleted
    FROM data_collection.source AS s
    WHERE s.base_id = p.source_base_id
    ORDER BY s.updated_at DESC
    LIMIT 1
) AS ls ON TRUE
ORDER BY p.base_id;
