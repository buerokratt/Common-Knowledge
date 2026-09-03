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
      - field: created_at
        type: string
        description: "Record creation timestamp"
      - field: updated_at
        type: string
        description: "Record last update timestamp"
*/
WITH
    _lock AS (
        -- Serialize concurrent create requests; second request blocks until first commits/rolls back
        SELECT pg_advisory_xact_lock(hashtext('single_agency_create'))
    ),
    _guard AS (
        -- Re-check inside the lock so a concurrent request that passed the Ruuter-level
        -- check but hasn't committed yet is still blocked
        SELECT EXISTS (
            SELECT 1
            FROM (
                SELECT DISTINCT ON (base_id) is_deleted
                FROM agency_management.agency
                ORDER BY base_id, updated_at DESC
            ) latest
            WHERE is_deleted = FALSE
        ) AS already_exists
    )
INSERT INTO agency_management.agency (name, sector)
SELECT :name, :sector
FROM _lock, _guard
WHERE NOT already_exists
RETURNING id, base_id, name, sector, created_at, updated_at;
