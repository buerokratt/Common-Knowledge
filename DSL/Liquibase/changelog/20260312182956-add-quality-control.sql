-- liquibase formatted sql
-- changeset ruwinirathnamalala:20260312182956 ignore:true
-- Create the quality_control_type enum (drop first if exists)

CREATE TYPE quality_control_type AS ENUM ('basic', 'comprehensive');

ALTER TABLE data_collection.source
ADD COLUMN IF NOT EXISTS quality_control quality_control_type DEFAULT NULL;

COMMENT ON COLUMN data_collection.source.quality_control IS 'Content extraction quality control method: basic, comprehensive, or NULL (fallback only)';