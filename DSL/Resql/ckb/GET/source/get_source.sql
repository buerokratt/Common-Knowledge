/*
declaration:
  version: 0.1
  description: "Get the most recently updated source by base_id"
  method: get
  namespace: source
  returns: json
  allowlist:
    query:
      - field: base_id
        type: string
        description: "UUID of the source's base_id"
  response:
    fields:
      - field: id
        type: string
        description: "Primary key of the source entry"
      - field: base_id
        type: string
        description: "Base identifier for the source"
      - field: url
        type: string
        description: "URL of the source"
      - field: subsector
        type: string
        description: "Subsector classification"
      - field: last_scraped_at
        type: timestamp
        description: "Timestamp of the last scraping operation"
      - field: status
        type: string
        description: "Status of the source"
      - field: agency_base_id
        type: string
        description: "Base ID of the associated agency"
      - field: cron_schedule
        type: string
        description: "Cron schedule for automated updates"
      - field: update_automatically
        type: boolean
        description: "Whether the source updates automatically"
      - field: created_at
        type: timestamp
        description: "Creation timestamp"
      - field: updated_at
        type: timestamp
        description: "Last update timestamp"
      - field: type
        type: string
        enum: ['url_to_scrape', 'file', 'api']
        description: "Type of the source"
      - field: is_stopping
        type: boolean
        description: "Whether the source is in stopping state"
      - field: quality_control
        type: string
        enum: ['basic', 'comprehensive']
        description: "Quality control method for content extraction"
      - field: extract_images
        type: boolean
        description: "Whether to extract images when cleaning"
*/
SELECT
    id, base_id, url, subsector, last_scraped_at, status, agency_base_id,
    cron_schedule, update_automatically, created_at, updated_at, type, is_stopping, quality_control, extract_images 
FROM data_collection.source 
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at) 
      FROM data_collection.source 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE LIMIT 1;