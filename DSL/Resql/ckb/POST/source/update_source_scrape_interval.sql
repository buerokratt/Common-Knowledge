SELECT copy_row_with_modifications(
    'source',
    'id', '::UUID', id::VARCHAR,
    CASE
        WHEN :updateAutomatically::BOOLEAN = FALSE OR cron_schedule != :cron_schedule
            THEN
                ARRAY[
                    'cron_schedule', '::TEXT', :cron_schedule,
                    'update_automatically', '::BOOLEAN', :updateAutomatically,
                    'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
                    'next_scrapping_at', '', NULL
                ]::VARCHAR[]
        ELSE
            ARRAY[
                'cron_schedule', '::TEXT', :cron_schedule,
                'update_automatically', '::BOOLEAN', :updateAutomatically,
                'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
            ]::VARCHAR[]
        END
) as id
FROM source
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM source 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;