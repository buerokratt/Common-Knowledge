-- liquibase formatted sql
-- changeset ahmer-mt:20250624225925 ignore:true
DROP TABLE IF EXISTS logs;
DROP TABLE IF EXISTS scrape_sources_task;
DROP TABLE IF EXISTS source_run;
DROP TABLE IF EXISTS source;
DROP TABLE IF EXISTS domain;
DROP TABLE IF EXISTS client;

DROP TYPE IF EXISTS status_type;
DROP TYPE IF EXISTS source_type;