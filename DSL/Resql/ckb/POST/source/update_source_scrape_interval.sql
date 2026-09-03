/*
declaration:
  version: 0.1
  description: "Update source cron schedule and update_automatically flags by base_id"
  method: post
  accepts: json
  returns: json
  namespace: source
  allowlist:
    body:
      - field: base_id
        type: string
        description: "Source base ID"
      - field: cron_schedule
        type: string
        description: "Cron schedule expression"
      - field: updateAutomatically
        type: boolean
        description: "Flag to update automatically"
      - field: qualityControl
        type: string
        description: "Quality control method (basic, comprehensive, or null)"
      - field: extractImages
        type: boolean
        description: "Whether to extract images when cleaning"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
*/
SELECT copy_row_with_modifications(
    'data_collection.source',
    'id', '::UUID', id::VARCHAR,
    CASE
        WHEN :updateAutomatically::BOOLEAN = FALSE OR cron_schedule != :cron_schedule
            THEN
                ARRAY[
                    'cron_schedule', '::TEXT', :cron_schedule,
                    'update_automatically', '::BOOLEAN', :updateAutomatically, 
                    'quality_control', '::quality_control_type', NULLIF(LOWER(TRIM(:qualityControl)), ''),
                    'extract_images', '::BOOLEAN', COALESCE(:extractImages::VARCHAR, 'false'),
                    'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
                    'next_scrapping_at', '', NULL
                ]::VARCHAR[]
        ELSE
            ARRAY[
                'cron_schedule', '::TEXT', :cron_schedule,
                'update_automatically', '::BOOLEAN', :updateAutomatically,
                'quality_control', '::quality_control_type', NULLIF(LOWER(TRIM(:qualityControl)), ''),
                'extract_images', '::BOOLEAN', COALESCE(:extractImages::VARCHAR, 'false'),
                'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR
            ]::VARCHAR[]
        END
) as id
FROM data_collection.source
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM data_collection.source 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;