-- liquibase formatted sql
-- changeset KoortHendrik:20261020120000-rollback ignore:true

DROP INDEX CONCURRENTLY IF EXISTS data_collection.idx_source_file_agency_base_updated;
