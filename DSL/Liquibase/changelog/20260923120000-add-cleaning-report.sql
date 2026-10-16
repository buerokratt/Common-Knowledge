-- liquibase formatted sql
-- changeset ckittask:20260923120000 ignore:true
-- Add cleaning_report table: one append-only row per cleaned file, recording
-- which extraction method produced the cleaned text and, when LLM quality
-- control is enabled, the LLM verdict, reason, issue category and token usage.

CREATE TABLE IF NOT EXISTS monitoring.cleaning_report (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_file_base_id UUID NOT NULL,
    source_base_id UUID NOT NULL,
    agency_base_id UUID NOT NULL,
    source_run_report_base_id UUID,
    url TEXT,
    file_type TEXT,
    quality_control quality_control_type DEFAULT NULL,
    evaluated_method TEXT,
    extraction_method TEXT NOT NULL,
    llm_model TEXT,
    llm_verdict TEXT CHECK (llm_verdict IN ('pass', 'fail', 'error')),
    llm_reason TEXT,
    llm_issue_category TEXT,
    llm_extract_status TEXT CHECK (llm_extract_status IN ('success', 'empty', 'error')),
    eval_prompt_tokens INTEGER,
    eval_completion_tokens INTEGER,
    extract_prompt_tokens INTEGER,
    extract_completion_tokens INTEGER,
    cleaned_data_url TEXT,
    deterministic_data_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_cleaning_report_source_file_created
    ON monitoring.cleaning_report (source_file_base_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cleaning_report_run_report
    ON monitoring.cleaning_report (source_run_report_base_id);

COMMENT ON TABLE monitoring.cleaning_report IS 'Per-file cleaning outcome: extraction method and LLM quality-control verdict';
COMMENT ON COLUMN monitoring.cleaning_report.evaluated_method IS 'Deterministic extractor whose output the LLM evaluated (trafilatura or beautifulsoup)';
COMMENT ON COLUMN monitoring.cleaning_report.extraction_method IS 'Extractor that produced the final cleaned text';
COMMENT ON COLUMN monitoring.cleaning_report.llm_verdict IS 'pass / fail from the LLM, or error when the evaluation call or its parsing failed';
COMMENT ON COLUMN monitoring.cleaning_report.llm_issue_category IS 'nav, cookie, footer, sidebar, truncated, structure or other; NULL on pass';
COMMENT ON COLUMN monitoring.cleaning_report.deterministic_data_url IS 'Deterministic extraction kept for comparison when comprehensive mode replaced it with the LLM re-extraction';
