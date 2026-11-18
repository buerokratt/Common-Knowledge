-- liquibase formatted sql
-- changeset ruwinirathnamalala:20261116120000 ignore:true
-- Rollback: restore edited content columns to source_file

ALTER TABLE data_collection.source_file ADD COLUMN IF NOT EXISTS edited_data_url TEXT;
ALTER TABLE data_collection.source_file ADD COLUMN IF NOT EXISTS edited_metadata_url TEXT;
