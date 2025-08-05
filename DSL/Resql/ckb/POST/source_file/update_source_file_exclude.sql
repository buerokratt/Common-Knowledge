SELECT copy_row_with_modifications(
    'source_file',
    'id', '::UUID', id::VARCHAR,
    ARRAY[
        'is_excluded', '::BOOLEAN', :excluded::TEXT,
        'updated_at', '::TIMESTAMP WITH TIME ZONE', NOW()::VARCHAR,
        'original_data_url', '::TEXT', 
            CASE 
                WHEN :excluded::BOOLEAN = TRUE THEN 
                    CASE 
                        WHEN original_data_url IS NULL THEN NULL
                        WHEN original_data_url LIKE 'excluded/%' THEN original_data_url
                        ELSE 'excluded/' || original_data_url
                    END
                ELSE 
                    CASE 
                        WHEN original_data_url IS NULL THEN NULL
                        WHEN original_data_url LIKE 'excluded/%' THEN SUBSTRING(original_data_url FROM 10)
                        ELSE original_data_url
                    END
            END::TEXT,
        'cleaned_data_url', '::TEXT',
            CASE 
                WHEN :excluded::BOOLEAN = TRUE THEN 
                    CASE 
                        WHEN cleaned_data_url IS NULL THEN NULL
                        WHEN cleaned_data_url LIKE 'excluded/%' THEN cleaned_data_url
                        ELSE 'excluded/' || cleaned_data_url
                    END
                ELSE 
                    CASE 
                        WHEN cleaned_data_url IS NULL THEN NULL
                        WHEN cleaned_data_url LIKE 'excluded/%' THEN SUBSTRING(cleaned_data_url FROM 10)
                        ELSE cleaned_data_url
                    END
            END::TEXT,
        'edited_data_url', '::TEXT',
            CASE 
                WHEN :excluded::BOOLEAN = TRUE THEN 
                    CASE 
                        WHEN edited_data_url IS NULL THEN NULL
                        WHEN edited_data_url LIKE 'excluded/%' THEN edited_data_url
                        ELSE 'excluded/' || edited_data_url
                    END
                ELSE 
                    CASE 
                        WHEN edited_data_url IS NULL THEN NULL
                        WHEN edited_data_url LIKE 'excluded/%' THEN SUBSTRING(edited_data_url FROM 10)
                        ELSE edited_data_url
                    END
            END::TEXT,
        'original_metadata_url', '::TEXT',
            CASE 
                WHEN :excluded::BOOLEAN = TRUE THEN 
                    CASE 
                        WHEN original_metadata_url IS NULL THEN NULL
                        WHEN original_metadata_url LIKE 'excluded/%' THEN original_metadata_url
                        ELSE 'excluded/' || original_metadata_url
                    END
                ELSE 
                    CASE 
                        WHEN original_metadata_url IS NULL THEN NULL
                        WHEN original_metadata_url LIKE 'excluded/%' THEN SUBSTRING(original_metadata_url FROM 10)
                        ELSE original_metadata_url
                    END
            END::TEXT,
        'cleaned_metadata_url', '::TEXT',
            CASE 
                WHEN :excluded::BOOLEAN = TRUE THEN 
                    CASE 
                        WHEN cleaned_metadata_url IS NULL THEN NULL
                        WHEN cleaned_metadata_url LIKE 'excluded/%' THEN cleaned_metadata_url
                        ELSE 'excluded/' || cleaned_metadata_url
                    END
                ELSE 
                    CASE 
                        WHEN cleaned_metadata_url IS NULL THEN NULL
                        WHEN cleaned_metadata_url LIKE 'excluded/%' THEN SUBSTRING(cleaned_metadata_url FROM 10)
                        ELSE cleaned_metadata_url
                    END
            END::TEXT,
        'edited_metadata_url', '::TEXT',
            CASE 
                WHEN :excluded::BOOLEAN = TRUE THEN 
                    CASE 
                        WHEN edited_metadata_url IS NULL THEN NULL
                        WHEN edited_metadata_url LIKE 'excluded/%' THEN edited_metadata_url
                        ELSE 'excluded/' || edited_metadata_url
                    END
                ELSE 
                    CASE 
                        WHEN edited_metadata_url IS NULL THEN NULL
                        WHEN edited_metadata_url LIKE 'excluded/%' THEN SUBSTRING(edited_metadata_url FROM 10)
                        ELSE edited_metadata_url
                    END
            END::TEXT
    ]::VARCHAR[]
) as id, source_base_id, agency_base_id
FROM source_file
WHERE base_id = :base_id::UUID
  AND updated_at = (
      SELECT MAX(updated_at)
      FROM source_file 
      WHERE base_id = :base_id::UUID
  )
  AND is_deleted = FALSE;