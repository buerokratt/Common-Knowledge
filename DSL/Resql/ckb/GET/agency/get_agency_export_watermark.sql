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
        description: "Latest timestamp across all source file versions and every deleted source version"
      - field: live_count
        type: integer
        description: "Number of documents whose latest version is not deleted and whose source is not deleted"
*/
-- Deleting a source marks only the source row (source/delete_source.sql), never
-- its files, so a deleted source version must move the watermark too. Only
-- deleted versions count: the scheduler rewrites live sources routinely, and
-- CKB never restores a source once it is marked deleted.
WITH latest_sources AS (
    SELECT DISTINCT ON (s.base_id)
        s.base_id,
        s.is_deleted
    FROM data_collection.source AS s
    WHERE s.agency_base_id = :agency_base_id::UUID
    ORDER BY s.base_id, s.updated_at DESC
),

latest_files AS (
    SELECT DISTINCT ON (sf.base_id)
        sf.base_id,
        sf.source_base_id,
        sf.is_deleted
    FROM data_collection.source_file AS sf
    WHERE sf.agency_base_id = :agency_base_id::UUID
    ORDER BY sf.base_id, sf.updated_at DESC
)

SELECT
    GREATEST(
        (
            SELECT MAX(sf.updated_at)
            FROM data_collection.source_file AS sf
            WHERE sf.agency_base_id = :agency_base_id::UUID
        ),
        (
            SELECT MAX(s.updated_at)
            FROM data_collection.source AS s
            WHERE
                s.agency_base_id = :agency_base_id::UUID
                AND s.is_deleted = TRUE
        )
    ) AS max_updated_at,
    (
        SELECT COUNT(*)
        FROM latest_files AS lf
        LEFT JOIN latest_sources AS ls ON lf.source_base_id = ls.base_id
        WHERE
            lf.is_deleted = FALSE
            AND ls.is_deleted IS NOT TRUE
    ) AS live_count;
