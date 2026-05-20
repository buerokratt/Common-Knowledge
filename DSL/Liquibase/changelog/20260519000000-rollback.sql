-- liquibase formatted sql
-- changeset ruwinirathnamalala:20260519000000-rollback ignore:true

ALTER TABLE data_collection.source
DROP COLUMN IF EXISTS extract_images;
