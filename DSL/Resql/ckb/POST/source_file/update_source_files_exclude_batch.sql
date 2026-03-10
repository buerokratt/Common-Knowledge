/*
declaration:
  version: 0.1
  description: "Set is_excluded flag and update URLs accordingly for multiple source_files by base_ids"
  method: post
  accepts: json
  returns: json
  namespace: source_file
  allowlist:
    body:
      - field: base_ids
        type: array
        description: "Array of source file base IDs"
      - field: excluded
        type: boolean
        description: "Exclusion flag"
  response:
    fields:
      - field: id
        type: string
        description: "Record ID"
      - field: source_base_id
        type: string
        description: "Source base ID"
      - field: agency_base_id
        type: string
        description: "Agency base ID"
*/
WITH ids AS (
  SELECT json_array_elements_text(COALESCE(ARRAY_TO_JSON(ARRAY[:base_ids]), '[]'::json))::UUID AS base_id
),
latest_files AS (
    SELECT base_id, MAX(updated_at) as max_updated_at
    FROM data_collection.source_file
    WHERE is_deleted = FALSE
    GROUP BY base_id
),
updated_files AS (
    SELECT 
        copy_row_with_modifications(
            'data_collection.source_file',
            'id', '::UUID', sf.id::VARCHAR,
            ARRAY[
                'is_excluded', '::BOOLEAN', :excluded::TEXT,
                'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
                'original_data_url', '::TEXT', 
                    CASE 
                        WHEN :excluded::BOOLEAN = TRUE THEN 
                            CASE 
                                WHEN sf.original_data_url IS NULL THEN NULL
                                WHEN sf.original_data_url LIKE 'excluded/%' THEN sf.original_data_url
                                ELSE 'excluded/' || sf.original_data_url
                            END
                        ELSE 
                            CASE 
                                WHEN sf.original_data_url IS NULL THEN NULL
                                WHEN sf.original_data_url LIKE 'excluded/%' THEN SUBSTRING(sf.original_data_url FROM 10)
                                ELSE sf.original_data_url
                            END
                    END::TEXT,
                'cleaned_data_url', '::TEXT',
                    CASE 
                        WHEN :excluded::BOOLEAN = TRUE THEN 
                            CASE 
                                WHEN sf.cleaned_data_url IS NULL THEN NULL
                                WHEN sf.cleaned_data_url LIKE 'excluded/%' THEN sf.cleaned_data_url
                                ELSE 'excluded/' || sf.cleaned_data_url
                            END
                        ELSE 
                            CASE 
                                WHEN sf.cleaned_data_url IS NULL THEN NULL
                                WHEN sf.cleaned_data_url LIKE 'excluded/%' THEN SUBSTRING(sf.cleaned_data_url FROM 10)
                                ELSE sf.cleaned_data_url
                            END
                    END::TEXT,
                'edited_data_url', '::TEXT',
                    CASE 
                        WHEN :excluded::BOOLEAN = TRUE THEN 
                            CASE 
                                WHEN sf.edited_data_url IS NULL THEN NULL
                                WHEN sf.edited_data_url LIKE 'excluded/%' THEN sf.edited_data_url
                                ELSE 'excluded/' || sf.edited_data_url
                            END
                        ELSE 
                            CASE 
                                WHEN sf.edited_data_url IS NULL THEN NULL
                                WHEN sf.edited_data_url LIKE 'excluded/%' THEN SUBSTRING(sf.edited_data_url FROM 10)
                                ELSE sf.edited_data_url
                            END
                    END::TEXT,
                'original_metadata_url', '::TEXT',
                    CASE 
                        WHEN :excluded::BOOLEAN = TRUE THEN 
                            CASE 
                                WHEN sf.original_metadata_url IS NULL THEN NULL
                                WHEN sf.original_metadata_url LIKE 'excluded/%' THEN sf.original_metadata_url
                                ELSE 'excluded/' || sf.original_metadata_url
                            END
                        ELSE 
                            CASE 
                                WHEN sf.original_metadata_url IS NULL THEN NULL
                                WHEN sf.original_metadata_url LIKE 'excluded/%' THEN SUBSTRING(sf.original_metadata_url FROM 10)
                                ELSE sf.original_metadata_url
                            END
                    END::TEXT,
                'cleaned_metadata_url', '::TEXT',
                    CASE 
                        WHEN :excluded::BOOLEAN = TRUE THEN 
                            CASE 
                                WHEN sf.cleaned_metadata_url IS NULL THEN NULL
                                WHEN sf.cleaned_metadata_url LIKE 'excluded/%' THEN sf.cleaned_metadata_url
                                ELSE 'excluded/' || sf.cleaned_metadata_url
                            END
                        ELSE 
                            CASE 
                                WHEN sf.cleaned_metadata_url IS NULL THEN NULL
                                WHEN sf.cleaned_metadata_url LIKE 'excluded/%' THEN SUBSTRING(sf.cleaned_metadata_url FROM 10)
                                ELSE sf.cleaned_metadata_url
                            END
                    END::TEXT,
                'edited_metadata_url', '::TEXT',
                    CASE 
                        WHEN :excluded::BOOLEAN = TRUE THEN 
                            CASE 
                                WHEN sf.edited_metadata_url IS NULL THEN NULL
                                WHEN sf.edited_metadata_url LIKE 'excluded/%' THEN sf.edited_metadata_url
                                ELSE 'excluded/' || sf.edited_metadata_url
                            END
                        ELSE 
                            CASE 
                                WHEN sf.edited_metadata_url IS NULL THEN NULL
                                WHEN sf.edited_metadata_url LIKE 'excluded/%' THEN SUBSTRING(sf.edited_metadata_url FROM 10)
                                ELSE sf.edited_metadata_url
                            END
                    END::TEXT
            ]::VARCHAR[]
        ) as id,
        sf.base_id,
        sf.source_base_id,
        sf.agency_base_id
    FROM data_collection.source_file sf
    INNER JOIN latest_files lf ON sf.base_id = lf.base_id AND sf.updated_at = lf.max_updated_at
    INNER JOIN ids ON sf.base_id = ids.base_id
    WHERE sf.is_excluded != :excluded::BOOLEAN
)
SELECT id, base_id, source_base_id, agency_base_id
FROM updated_files;
