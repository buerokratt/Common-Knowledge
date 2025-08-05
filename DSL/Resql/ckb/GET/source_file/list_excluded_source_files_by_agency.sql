SELECT 
    id, 
    base_id, 
    agency_base_id, 
    source_base_id
FROM source_file
WHERE 
    agency_base_id = :agency_base_id::UUID 
    AND is_excluded = true 
    AND is_deleted = false;