-- liquibase formatted sql
-- changeset KoortHendrik:20261020120000 ignore:true

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_source_file_agency_base_updated
    ON data_collection.source_file (agency_base_id, base_id, updated_at DESC);
