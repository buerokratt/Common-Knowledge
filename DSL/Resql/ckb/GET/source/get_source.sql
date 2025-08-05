SELECT 
    id, base_id, url, subsector, last_scraped_at, status, agency_base_id,
    cron_schedule, update_automatically, created_at, updated_at, type, is_stopping
FROM source 
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM source 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE LIMIT 1;