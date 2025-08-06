-- liquibase formatted sql
-- changeset ahmer-mt:20250624225925 ignore:true

DROP TABLE IF EXISTS source_file CASCADE;
DROP TABLE IF EXISTS source_run_page CASCADE;
DROP TABLE IF EXISTS source_run_report CASCADE;
DROP TABLE IF EXISTS source CASCADE;
DROP TABLE IF EXISTS agency CASCADE;

-- Drop custom ENUM types
DROP TYPE IF EXISTS source_file_type;
DROP TYPE IF EXISTS source_file_status_type;
DROP TYPE IF EXISTS source_status_type;
DROP TYPE IF EXISTS source_type;
DROP TYPE IF EXISTS agency_type;
