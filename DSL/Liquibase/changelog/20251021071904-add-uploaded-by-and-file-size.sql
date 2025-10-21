-- liquibase formatted sql
-- changeset ahmer-mt:20251021071904 ignore:true
-- Add uploaded_by and file_size columns to source_file table

ALTER TABLE data_collection.source_file
ADD COLUMN uploaded_by TEXT,
ADD COLUMN file_size BIGINT;

COMMENT ON COLUMN data_collection.source_file.uploaded_by IS 'User/system that uploaded the file (only for uploaded_file type)';
COMMENT ON COLUMN data_collection.source_file.file_size IS 'File size in bytes';
