/*
declaration:
  version: 0.1
  description: "Check if agency name already exists (excluding deleted and optionally excluding a specific agency)"
  method: get
  accepts: json
  returns: json
  namespace: agency
  allowlist:
    parameters:
      - field: name
        type: string
        description: "Agency name to check"
      - field: exclude_base_id
        type: string
        description: "Base ID to exclude from check (for edit operations)"
        required: false
  response:
    fields:
      - field: exists
        type: boolean
        description: "Whether an agency with this name exists"
*/

WITH latest_agencies AS (
    SELECT DISTINCT ON (base_id)
        base_id,
        name,
        is_deleted,
        updated_at
    FROM agency_management.agency
    ORDER BY base_id, updated_at DESC
),
matching_agency AS (
    SELECT base_id
    FROM latest_agencies
    WHERE LOWER(TRIM(name)) = LOWER(TRIM(:name))
      AND is_deleted = FALSE
      AND (:exclude_base_id IS NULL OR :exclude_base_id = '' OR base_id::TEXT != :exclude_base_id)
    LIMIT 1
)
SELECT 
    CASE WHEN COUNT(*) > 0 THEN TRUE ELSE FALSE END as exists
FROM matching_agency;