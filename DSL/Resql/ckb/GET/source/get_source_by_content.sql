SELECT id, base_id, agency_base_id, url, subsector, type, status
FROM source
WHERE (base_id, updated_at) IN (
    SELECT base_id, max(updated_at)
    FROM source
    GROUP BY base_id
) AND is_deleted = FALSE
    AND agency_base_id = :agency_base_id::UUID
    AND url = :url
    AND subsector = :subsector
    AND type = :type::SOURCE_TYPE
LIMIT 1;
