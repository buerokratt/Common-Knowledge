-- liquibase formatted sql
-- changeset schema-001:20250821160758 ignore:true

-- SCHEMA-001: Implement explicit schemas according to Buerokratt ADR
-- Version: SCHEMA-001

-- Create schemas based on functional areas
CREATE SCHEMA IF NOT EXISTS agency_management;
CREATE SCHEMA IF NOT EXISTS data_collection;
CREATE SCHEMA IF NOT EXISTS monitoring;

-- Move tables to their respective schemas
ALTER TABLE IF EXISTS public.agency SET SCHEMA agency_management;
ALTER TABLE IF EXISTS public.source SET SCHEMA data_collection;
ALTER TABLE IF EXISTS public.source_file SET SCHEMA data_collection;
ALTER TABLE IF EXISTS public.source_run_report SET SCHEMA monitoring;
ALTER TABLE IF EXISTS public.source_run_page SET SCHEMA monitoring;

-- Grant permissions on schemas
GRANT USAGE ON SCHEMA agency_management TO PUBLIC;
GRANT USAGE ON SCHEMA data_collection TO PUBLIC;
GRANT USAGE ON SCHEMA monitoring TO PUBLIC;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA agency_management TO PUBLIC;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA data_collection TO PUBLIC;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA monitoring TO PUBLIC;

-- Revoke CREATE on public schema to prevent future usage
REVOKE CREATE ON SCHEMA public FROM public;