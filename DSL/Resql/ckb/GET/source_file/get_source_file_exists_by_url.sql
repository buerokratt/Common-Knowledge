/*
declaration:
  version: 0.1
  description: "Check if a source_file already exists (or has ever existed, including deleted) for a source by URL -- used by sitemap discovery to decide whether a URL is genuinely new. A deleted file must still count as \"exists\" here, otherwise a page a user explicitly removed gets silently re-created as a brand-new record the next time the source is discovered/refreshed."
  method: get
  namespace: source_file
  returns: json
  allowlist:
    query:
      - field: source_base_id
        type: string
        description: "Base ID of the source"
      - field: url
        type: string
        description: "URL to check"
  response:
    fields:
      - field: exists
        type: boolean
        description: "Whether a matching source_file exists (deleted or not)"
*/
SELECT count(*) > 0 AS exists
FROM data_collection.source_file
WHERE (base_id, updated_at) IN (
    SELECT base_id, max(updated_at)
    FROM data_collection.source_file
    WHERE source_base_id = :source_base_id::UUID
    GROUP BY base_id
) AND source_base_id = :source_base_id::UUID
    AND url = :url;
