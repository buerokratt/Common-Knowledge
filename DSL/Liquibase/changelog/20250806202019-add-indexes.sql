-- liquibase formatted sql
-- changeset ahmer-mt:20250806202019 ignore:true

-- AGENCY TABLE INDEXES
CREATE INDEX idx_agency_base_deleted_updated ON agency (base_id, is_deleted, updated_at);
CREATE INDEX idx_agency_base_updated_deleted_all ON agency (base_id, updated_at DESC, is_deleted, zip_dirty, is_zipping, type, name, sector);

-- SOURCE TABLE INDEXES
CREATE INDEX idx_source_base_updated_deleted_agency_url_subsector_type ON source (base_id, updated_at DESC, is_deleted, agency_base_id, url, subsector, type);
CREATE INDEX idx_source_base_updated_deleted_auto_scrapping_status ON source (base_id, updated_at DESC, is_deleted, update_automatically, next_scrapping_at, status);
CREATE INDEX idx_source_base_deleted_updated ON source (base_id, is_deleted, updated_at);
CREATE INDEX idx_source_agency_base_updated_deleted_url_subsector_scraped_status ON source (agency_base_id, base_id, updated_at DESC, is_deleted, url, subsector, last_scraped_at, status);
CREATE INDEX idx_source_type_base_updated_deleted_url_scraped_status ON source (type, base_id, updated_at DESC, is_deleted, url, last_scraped_at, status);

-- SOURCE_FILE TABLE INDEXES
CREATE INDEX idx_source_file_base_deleted_updated ON source_file (base_id, is_deleted, updated_at);
CREATE INDEX idx_source_file_agency_excluded_deleted ON source_file (agency_base_id, is_excluded, is_deleted);
CREATE INDEX idx_source_file_type_base_updated_deleted_url_title_excluded_status_scraped_external ON source_file (type, base_id, updated_at DESC, is_deleted, url, page_title, is_excluded, status, last_scraped_at, external_id);
CREATE INDEX idx_source_file_type_source_base_updated_deleted_url_title_excluded_status_scraped_external ON source_file (type, source_base_id, base_id, updated_at DESC, is_deleted, url, page_title, is_excluded, status, last_scraped_at, external_id);
CREATE INDEX idx_source_file_type_base_updated_deleted_filename_subsector_excluded_created ON source_file (type, base_id, updated_at DESC, is_deleted, file_name, subsector, is_excluded, created_at);
CREATE INDEX idx_source_file_type_source_base_updated_deleted_filename_subsector_excluded_status_created ON source_file (type, source_base_id, base_id, updated_at DESC, is_deleted, file_name, subsector, is_excluded, status, created_at);

-- SOURCE_RUN_REPORT TABLE INDEXES
CREATE INDEX idx_source_run_report_base_deleted_updated ON source_run_report (base_id, is_deleted, updated_at);
CREATE INDEX idx_source_run_report_base_updated_deleted_agency_url_errors_started_finished ON source_run_report (base_id, updated_at DESC, is_deleted, agency_name, url, errors, scraping_started_at, scraping_finished_at);

-- SOURCE_RUN_PAGE TABLE INDEXES
CREATE INDEX idx_source_run_page_report_base_updated_deleted_url_error_type_message_scraped ON source_run_page (source_run_report_base_id, base_id, updated_at DESC, is_deleted, url, error_type, error_message, scraped_at);