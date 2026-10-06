/*
declaration:
  version: 0.1
  description: "Insert a cleaning report row for a cleaned source file"
  method: post
  accepts: json
  returns: json
  namespace: cleaning_report
  allowlist:
    body:
      - field: source_file_base_id
        type: string
        description: "Source file base ID"
      - field: source_base_id
        type: string
        description: "Source base ID"
      - field: agency_base_id
        type: string
        description: "Agency base ID"
      - field: source_run_report_base_id
        type: string
        description: "Source run report base ID"
      - field: url
        type: string
        description: "Cleaned URL"
      - field: file_type
        type: string
        description: "File extension, e.g. .html"
      - field: quality_control
        type: string
        description: "basic, comprehensive or empty"
      - field: evaluated_method
        type: string
        description: "Extractor whose output the LLM evaluated"
      - field: extraction_method
        type: string
        description: "Extractor that produced the final cleaned text"
      - field: llm_model
        type: string
        description: "Azure OpenAI deployment name"
      - field: llm_verdict
        type: string
        description: "pass, fail, error or empty"
      - field: llm_reason
        type: string
        description: "LLM one-sentence reason"
      - field: llm_issue_category
        type: string
        description: "Issue category on fail"
      - field: llm_extract_status
        type: string
        description: "success, empty, error or empty string"
      - field: eval_prompt_tokens
        type: string
        description: "Evaluation prompt tokens"
      - field: eval_completion_tokens
        type: string
        description: "Evaluation completion tokens"
      - field: extract_prompt_tokens
        type: string
        description: "Re-extraction prompt tokens"
      - field: extract_completion_tokens
        type: string
        description: "Re-extraction completion tokens"
      - field: cleaned_data_url
        type: string
        description: "Cleaned text URL this report describes"
      - field: deterministic_data_url
        type: string
        description: "Deterministic extraction URL (comprehensive + LLM re-extract only)"
*/
INSERT INTO monitoring.cleaning_report (
    source_file_base_id,
    source_base_id,
    agency_base_id,
    source_run_report_base_id,
    url,
    file_type,
    quality_control,
    evaluated_method,
    extraction_method,
    llm_model,
    llm_verdict,
    llm_reason,
    llm_issue_category,
    llm_extract_status,
    eval_prompt_tokens,
    eval_completion_tokens,
    extract_prompt_tokens,
    extract_completion_tokens,
    cleaned_data_url,
    deterministic_data_url
)
VALUES (
    :source_file_base_id::UUID,
    :source_base_id::UUID,
    :agency_base_id::UUID,
    NULLIF(:source_run_report_base_id, '')::UUID,
    NULLIF(:url, ''),
    NULLIF(:file_type, ''),
    NULLIF(:quality_control, '')::quality_control_type,
    NULLIF(:evaluated_method, ''),
    :extraction_method,
    NULLIF(:llm_model, ''),
    NULLIF(:llm_verdict, ''),
    NULLIF(:llm_reason, ''),
    NULLIF(:llm_issue_category, ''),
    NULLIF(:llm_extract_status, ''),
    NULLIF(:eval_prompt_tokens, '')::INTEGER,
    NULLIF(:eval_completion_tokens, '')::INTEGER,
    NULLIF(:extract_prompt_tokens, '')::INTEGER,
    NULLIF(:extract_completion_tokens, '')::INTEGER,
    NULLIF(:cleaned_data_url, ''),
    NULLIF(:deterministic_data_url, '')
)
RETURNING id;
