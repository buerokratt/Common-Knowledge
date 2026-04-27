/*
declaration:
  version: 0.1
  description: "Check if any agency exists in this deployment"
  method: get
  namespace: agency
  returns: json
  allowlist:
    query: []
  response:
    fields:
      - field: exists
        type: boolean
        description: "True if at least one non-deleted agency exists"
*/
SELECT EXISTS (
    SELECT 1
    FROM (
        SELECT DISTINCT ON (base_id) is_deleted
        FROM agency_management.agency
        ORDER BY base_id, updated_at DESC
    ) latest
    WHERE is_deleted = FALSE
) AS exists;
