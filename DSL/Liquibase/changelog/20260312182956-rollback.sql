-- liquibase formatted sql
-- changeset ruwinirathnamalala:20260312182956 ignore:true
-- Rollback: Drop the quality_control column and enum type
ALTER TABLE data_collection.source
DROP COLUMN IF EXISTS quality_control;

DROP TYPE IF EXISTS quality_control_type;
