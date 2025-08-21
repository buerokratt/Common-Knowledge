/*
declaration:
  version: 0.1
  description: "Get latest uploaded files by list of source file IDs with pagination and sorting"
  method: get
  namespace: source_file
  returns: json
  allowlist:
    query:
      - field: source_file_ids
        type: string
        description: "Comma-separated list of source file base IDs (UUIDs)"
      - field: page
        type: integer
        description: "Current page number"
      - field: page_size
        type: integer
        description: "Number of results per page"
      - field: total_count
        type: integer
        description: "Total number of matching items"
      - field: sorting
        type: string
        enum: [
          "file_name asc", "file_name desc",
          "subsector asc", "subsector desc",
          "is_excluded asc", "is_excluded desc",
          "created_at asc", "created_at desc"
        ]
        description: "Sorting rule"
  response:
    fields:
      - field: id
        type: integer
        description: "Primary key of the uploaded file"
      - field: base_id
        type: string
        description: "Base ID of the file"
      - field: source_base_id
        type: string
        description: "Base ID of the source"
      - field: file_name
        type: string
        description: "Name of the uploaded file"
      - field: subsector
        type: string
        description: "Subsector of the file content"
      - field: original_data_url
        type: string
        description: "URL to the original data"
      - field: cleaned_data_url
        type: string
        description: "URL to the cleaned data"
      - field: edited_data_url
        type: string
        description: "URL to the edited data"
      - field: is_excluded
        type: boolean
        description: "Whether the file is excluded"
      - field: created_at
        type: timestamp
        description: "When the file was created"
      - field: updated_at
        type: timestamp
        description: "When the file was last updated"
      - field: status
        type: string
        enum: ['scraped_file', 'uploaded_file', 'api_file']
        description: "Status of the uploaded file"
      - field: page
        type: integer
        description: "Current page number"
      - field: total_pages
        type: integer
        description: "Total number of pages"
      - field: total
        type: integer
        description: "Total number of results"
*/
WITH latest_files AS (
    SELECT DISTINCT ON (base_id) 
        id, base_id, source_base_id, file_name, subsector, original_data_url, cleaned_data_url, 
        edited_data_url, is_excluded, created_at, updated_at, status, is_deleted
    FROM data_collection.source_file 
    WHERE type = 'uploaded_file'
      AND base_id = ANY(string_to_array(:source_file_ids, ',')::UUID[])
    ORDER BY base_id, updated_at DESC
)
SELECT 
    id, base_id, source_base_id, file_name, subsector, original_data_url, cleaned_data_url, 
    edited_data_url, is_excluded, created_at, updated_at, status,
    :page as page,
    CEIL(:total_count::DECIMAL / :page_size::DECIMAL) AS total_pages,
    :total_count AS total
FROM latest_files
WHERE is_deleted = FALSE
ORDER BY 
    CASE WHEN :sorting = 'file_name asc' THEN file_name END ASC,
    CASE WHEN :sorting = 'file_name desc' THEN file_name END DESC,
    CASE WHEN :sorting = 'subsector asc' THEN subsector END ASC,
    CASE WHEN :sorting = 'subsector desc' THEN subsector END DESC,
    CASE WHEN :sorting = 'is_excluded asc' THEN is_excluded END ASC,
    CASE WHEN :sorting = 'is_excluded desc' THEN is_excluded END DESC,
    CASE WHEN :sorting = 'created_at asc' THEN created_at END ASC,
    CASE WHEN :sorting = 'created_at desc' THEN created_at END DESC,
    created_at DESC NULLS LAST;