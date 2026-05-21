-- liquibase formatted sql
-- changeset ruwinirathnamalala:20260518120000
-- Remove external_id column from agency table (CentOps integration removed)

ALTER TABLE agency_management.agency DROP COLUMN IF EXISTS external_id;
