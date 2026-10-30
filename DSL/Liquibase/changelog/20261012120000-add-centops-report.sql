-- liquibase formatted sql
-- changeset ruwinirathnamalala:20261012120000 ignore:true
-- Add centops_report table: one row per agency, recording the checksum and
-- link expiry last sent to CentOps, so the hourly job knows when to send again.

CREATE TABLE IF NOT EXISTS agency_management.centops_report (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agency_base_id UUID NOT NULL UNIQUE,
    reported_data_hash TEXT NOT NULL,
    presigned_url_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reported_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE agency_management.centops_report IS 'Last successful report of each agency to CentOps';
COMMENT ON COLUMN agency_management.centops_report.agency_base_id IS 'agency_management.agency.base_id';
COMMENT ON COLUMN agency_management.centops_report.reported_data_hash IS 'data_hash last sent to CentOps';
COMMENT ON COLUMN agency_management.centops_report.presigned_url_expires_at IS 'When the presigned link last sent to CentOps expires';
COMMENT ON COLUMN agency_management.centops_report.reported_at IS 'When it was sent';
