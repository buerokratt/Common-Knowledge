/*
declaration:
  version: 0.1
  description: "Get latest non-deleted scraped pages by list of source file IDs with sorting"
  method: get
  namespace: source_file
  returns: json
  allowlist:
    query:
      - field: type
        type: string
        description: "Type of source file"
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
          "url asc", "url desc",
          "page_title asc", "page_title desc",
          "excluded asc", "excluded desc",
          "status asc", "status desc",
          "last_scraped_at asc", "last_scraped_at desc",
          "external_id asc", "external_id desc"
        ]
        description: "Sorting rule"
  response:
    fields:
      - field: id
        type: integer
        description: "Primary key of the source file"
      - field: base_id
        type: string
        description: "Base ID of the source file"
      - field: source_base_id
        type: string
        description: "Base ID of the source"
      - field: url
        type: string
        description: "URL of the scraped page"
      - field: page_title
        type: string
        description: "Title of the scraped page"
      - field: status
        type: string
        enum: ['scraped_file', 'uploaded_file', 'api_file']
        description: "Scraping status"
      - field: external_id
        type: string
        description: "External identifier"
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
      - field: updated_at
        type: timestamp
        description: "Last update timestamp"
      - field: originally_scraped
        type: timestamp
        description: "When the page was originally scraped"
      - field: last_scraped_at
        type: timestamp
        description: "When the page was last scraped"
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
WITH latest_scraped_pages AS (
    SELECT DISTINCT ON (base_id) 
        id, base_id, source_base_id, url, page_title, status, 
        original_data_url, cleaned_data_url, edited_data_url, external_id,
        is_excluded, updated_at, originally_scraped, last_scraped_at, is_deleted
    FROM data_collection.source_file 
    WHERE type = :type::source_file_type
      AND base_id = ANY(string_to_array(:source_file_ids, ',')::UUID[])
    ORDER BY base_id, updated_at DESC
)
SELECT 
    id, base_id, source_base_id, url, page_title, status, external_id,
    original_data_url, cleaned_data_url, edited_data_url, is_excluded, updated_at, originally_scraped, last_scraped_at,
    :page as page,
    CEIL(:total_count::DECIMAL / :page_size::DECIMAL) AS total_pages,
    :total_count AS total
FROM latest_scraped_pages
WHERE is_deleted = FALSE
ORDER BY 
    CASE WHEN :sorting = 'url asc' THEN url END ASC,
    CASE WHEN :sorting = 'url desc' THEN url END DESC,
    CASE WHEN :sorting = 'page_title asc' THEN page_title END ASC,
    CASE WHEN :sorting = 'page_title desc' THEN page_title END DESC,
    CASE WHEN :sorting = 'excluded asc' THEN is_excluded END ASC,
    CASE WHEN :sorting = 'excluded desc' THEN is_excluded END DESC,
    CASE WHEN :sorting = 'status asc' THEN status END ASC,
    CASE WHEN :sorting = 'status desc' THEN status END DESC,
    CASE WHEN :sorting = 'last_scraped_at asc' THEN last_scraped_at END ASC,
    CASE WHEN :sorting = 'last_scraped_at desc' THEN last_scraped_at END DESC,
    CASE WHEN :sorting = 'external_id asc' THEN external_id END ASC,
    CASE WHEN :sorting = 'external_id desc' THEN external_id END DESC,
    last_scraped_at DESC NULLS LAST;