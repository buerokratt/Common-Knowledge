-- liquibase formatted sql
-- changeset ahmer-mt:20250624225925 ignore:true
-- Initial Migration for Data Collection System
-- Version: 001_initial_schema
-- Created: 2025-01-01

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE EXTENSION IF NOT EXISTS hstore;

-- Create custom ENUM types
CREATE TYPE agency_type AS ENUM ('client', 'api');
CREATE TYPE source_type AS ENUM ('url_to_scrape', 'file', 'api');
CREATE TYPE source_status_type AS ENUM ('new', 'running', 'finished', 'failed');
CREATE TYPE source_file_status_type AS ENUM ('scraping', 'cleaning', 'finished', 'not_found', 'failed');
CREATE TYPE source_file_type AS ENUM ('scraped_file', 'uploaded_file', 'api_file');

CREATE TABLE agency (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    base_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    sector TEXT,
    external_id TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    zipped_data_url TEXT,
    zip_dirty BOOLEAN DEFAULT FALSE,
    is_zipping BOOLEAN DEFAULT FALSE,
    type agency_type NOT NULL DEFAULT 'client',
    data_hash TEXT
);

CREATE TABLE source (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    base_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    agency_base_id UUID NOT NULL,
    url TEXT,
    subsector TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_scraped_at TIMESTAMP WITH TIME ZONE,
    next_scrapping_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    is_deleted BOOLEAN DEFAULT FALSE,
    is_stopping BOOLEAN DEFAULT FALSE,
    created_by TEXT,
    type source_type NOT NULL,
    status source_status_type NOT NULL DEFAULT 'running',
    update_automatically BOOLEAN,
    cron_schedule TEXT
);


CREATE TABLE source_run_report (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    base_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    agency_base_id UUID NOT NULL,
    source_base_id UUID NOT NULL,
    agency_name TEXT,
    url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    scraping_started_at TIMESTAMP WITH TIME ZONE,
    scraping_finished_at TIMESTAMP WITH TIME ZONE,
    errors INTEGER DEFAULT 0,
    scraping_log_url TEXT,
    cleaning_log_url TEXT,
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE TABLE source_run_page (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    base_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    agency_base_id UUID NOT NULL,
    source_base_id UUID NOT NULL,
    source_run_report_base_id UUID NOT NULL,
    url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    scraped_at TIMESTAMP WITH TIME ZONE,
    error_type TEXT,
    error_message TEXT,
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE TABLE source_file (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    base_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    source_base_id UUID NOT NULL,
    agency_base_id UUID NOT NULL,
    url TEXT,
    page_title TEXT,
    original_data_url TEXT,
    cleaned_data_url TEXT,
    edited_data_url TEXT,
    original_metadata_url TEXT,
    cleaned_metadata_url TEXT,
    edited_metadata_url TEXT,
    original_data_hash TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_scraped_at TIMESTAMP WITH TIME ZONE,
    originally_scraped TIMESTAMP WITH TIME ZONE,
    status source_file_status_type NOT NULL DEFAULT 'cleaning',
    type source_file_type NOT NULL,
    file_name TEXT,
    external_id TEXT,
    subsector TEXT,
    is_excluded BOOLEAN DEFAULT FALSE,
    is_deleted BOOLEAN DEFAULT FALSE
);