-- liquibase formatted sql
-- changeset ruwinirathnamalala:20260519000000 ignore:true
-- Add extract_images flag to source table

ALTER TABLE data_collection.source
ADD COLUMN IF NOT EXISTS extract_images BOOLEAN NOT NULL DEFAULT FALSE;