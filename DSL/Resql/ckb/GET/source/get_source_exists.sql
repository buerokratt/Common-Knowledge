/*
declaration:
  version: 0.1
  description: "Check if a source exists by agency_base_id, url, subsector, and type"
  method: get
  namespace: source
  returns: json
  allowlist:
    query:
      - field: agency_base_id
        type: string
        description: "Base ID of the associated agency"
      - field: url
        type: string
        description: "URL of the source"
      - field: subsector
        type: string
        description: "Subsector classification"
      - field: type
        type: string
        enum: ['url_to_scrape', 'file', 'api']
        description: "Type of the source"
  response:
    fields:
      - field: exists
        type: boolean
        description: "Whether a matching source exists"
*/
SELECT count(*) > 0 AS exists
FROM data_collection.source
WHERE (base_id, updated_at) IN (
    SELECT base_id, max(updated_at)
    FROM data_collection.source
    GROUP BY base_id
) AND is_deleted = FALSE
    AND agency_base_id = :agency_base_id::UUID
    AND url = :url
    AND subsector = :subsector
    AND type = :type::SOURCE_TYPE;
