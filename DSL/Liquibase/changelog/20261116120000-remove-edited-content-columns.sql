-- liquibase formatted sql
-- changeset ruwinirathnamalala:20261116120000
-- Remove edited content columns from source_file (manual editing removed, #472)

ALTER TABLE data_collection.source_file DROP COLUMN IF EXISTS edited_data_url;
ALTER TABLE data_collection.source_file DROP COLUMN IF EXISTS edited_metadata_url;
