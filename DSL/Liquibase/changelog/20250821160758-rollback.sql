-- liquibase formatted sql
-- Rollback script for SCHEMA-001 implementation
-- This script reverts the schema changes and moves tables back to public schema

-- Move tables back to public schema
ALTER TABLE IF EXISTS agency_management.agency SET SCHEMA public;
ALTER TABLE IF EXISTS data_collection.source SET SCHEMA public;
ALTER TABLE IF EXISTS data_collection.source_file SET SCHEMA public;
ALTER TABLE IF EXISTS monitoring.source_run_report SET SCHEMA public;
ALTER TABLE IF EXISTS monitoring.source_run_page SET SCHEMA public;

-- Drop schemas (will only succeed if empty)
DROP SCHEMA IF EXISTS monitoring;
DROP SCHEMA IF EXISTS data_collection;
DROP SCHEMA IF EXISTS agency_management;

-- Restore public schema permissions
GRANT CREATE ON SCHEMA public TO public;