-- liquibase formatted sql
-- changeset ruwinirathnamalala:20260518120000 ignore:true
-- Rollback: restore external_id column to agency table

ALTER TABLE agency_management.agency ADD COLUMN IF NOT EXISTS external_id TEXT;
