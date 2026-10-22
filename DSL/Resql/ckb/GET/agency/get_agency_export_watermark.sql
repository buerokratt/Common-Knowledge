/*
declaration:
  version: 0.1
  description: "Include deleted versions so removals advance the export watermark"
  method: get
  namespace: agency
  returns: json
  allowlist:
    query:
      - field: agency_base_id
        type: string
        description: "UUID of the agency base"
  response:
    fields:
      - field: max_updated_at
        type: timestamp
        description: "Latest timestamp across all source file versions"
      - field: live_count
        type: integer
        description: "Number of documents whose latest version is not deleted"
*/
WITH latest_files AS (
    SELECT DISTINCT ON (base_id)
        base_id, is_deleted
    FROM data_collection.source_file
    WHERE agency_base_id = :agency_base_id::UUID
    ORDER BY base_id, updated_at DESC
)
SELECT
    MAX(updated_at) AS max_updated_at,
    (SELECT COUNT(*) FROM latest_files WHERE is_deleted = false) AS live_count
FROM data_collection.source_file
WHERE agency_base_id = :agency_base_id::UUID;
