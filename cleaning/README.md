# Cleaning Service

A FastAPI microservice that processes and cleans scraped web content into text suitable for LLM consumption. The service supports HTML, PDF, DOCX, DOC, and PPTX formats, with optional LLM-assisted extraction and image extraction.

## Architecture

```
cleaning/
├── api/
│   ├── app.py            # API endpoints
│   ├── models.py         # Pydantic request models
│   ├── config.py         # Configuration & Vault secrets
│   └── __init__.py
├── worker/
│   ├── tasks.py          # Core cleaning logic
│   ├── utils.py          # Error handling & cleanup utilities
│   └── __init__.py
├── requirements.txt
└── Dockerfile
```

## Features

- **Multi-format support**: HTML, PDF, DOCX, DOC, PPTX
- **HTML extraction strategies**: trafilatura (primary) → optional LLM evaluation/correction → BeautifulSoup fallback
- **Image extraction**: from HTML (data-URIs and remote URLs), PDF, DOCX, and PPTX
- **Language detection**: automatic detection via langdetect
- **Batch processing**: async endpoint for processing multiple files in one request
- **LLM integration**: optional Azure OpenAI-powered content evaluation and re-extraction

## API Endpoints

### POST /clean_file

Synchronous. Cleans a single file and blocks until complete (timeout: 10 minutes).

**Request body:**

```json
{
  "file_path": "/scrapped-data/.../file.html",
  "meta_data_path": "/scrapped-data/.../file.meta.json",
  "directory_path": "/scrapped-data/.../",
  "source_file_id": "uuid",
  "url": "https://source-url.com",
  "logs_path": "/scrapped-data/.../logfile.log",
  "use_llm": false,
  "use_llm_correction": false,
  "extract_images": false
}
```

**Response:** HTTP 200 on success. Writes `cleaned.txt` and `cleaned.meta.json` to the working directory and updates the database via Ruuter.

---

### POST /clean_source_async

Asynchronous. Accepts a batch of files and returns immediately. Processing runs in the background.

**Request body:**

```json
{
  "source_base_id": "uuid",
  "agency_base_id": "uuid",
  "source_run_report_base_id": "uuid",
  "scraping_log_url": "",
  "logs_path": "/scrapped-data/.../logfile.log",
  "use_llm": false,
  "use_llm_correction": false,
  "extract_images": false,
  "files": [
    {
      "baseId": "uuid",
      "sourceBaseId": "uuid",
      "url": "https://source-url.com",
      "originalDataUrl": "/scrapped-data/.../file.html",
      "originalMetadataUrl": "/scrapped-data/.../file.meta.json"
    }
  ]
}
```

**Response:** HTTP 200 immediately. Check Ruuter for status updates.

## Data Models

### EntityToClean (single file)

| Field                          | Type | Required | Description                                               |
| ------------------------------ | ---- | -------- | --------------------------------------------------------- |
| `file_path`                  | path | yes      | Path to the file to clean                                 |
| `meta_data_path`             | path | yes      | Path to the metadata JSON                                 |
| `directory_path`             | path | yes      | Working directory                                         |
| `source_file_id`             | str  | yes      | Unique file identifier                                    |
| `source_base_id`             | str  | yes      | Parent source identifier                                  |
| `agency_base_id`             | str  | yes      | Agency identifier                                         |
| `source_run_report_base_id`  | str  | yes      | Run report identifier (used for error reporting)          |
| `url`                        | str  | yes      | Original source URL                                       |
| `logs_path`                  | path | yes      | Log file path                                             |
| `use_llm`                    | bool | no       | Enable LLM-based content evaluation (default: false)      |
| `use_llm_correction`         | bool | no       | Enable LLM full re-extraction on failure (default: false) |
| `extract_images`             | bool | no       | Extract and upload images (default: false)                |

All paths are validated to stay within `/scrapped-data` to prevent directory traversal.

## Processing Flow

1. Receive cleaning request with file paths and options
2. Detect content type from metadata
3. Extract text by format:
   - **HTML**: trafilatura → (optional LLM eval) → (optional LLM re-extraction) → BeautifulSoup fallback
   - **PDF**: pymupdf4llm (Markdown output)
   - **DOCX/DOC/PPTX**: Unstructured library
4. Normalize newlines
5. Detect language
6. Upload cleaned text and updated metadata to Ruuter
7. Extract and upload images if `extract_images=true`
8. Update database record via Ruuter
9. Clean up working directory

### HTML Cleaning Modes

| `use_llm` | `use_llm_correction` | Behavior                                                                          |
| ----------- | ---------------------- | --------------------------------------------------------------------------------- |
| false       | false                  | trafilatura → BeautifulSoup fallback                                             |
| true        | false                  | trafilatura → LLM evaluation → BeautifulSoup if LLM rejects                     |
| true        | true                   | trafilatura → LLM evaluation → LLM full re-extraction → BeautifulSoup fallback |

## Environment Variables

| Variable              | Required       | Description                                                                             |
| --------------------- | -------------- | --------------------------------------------------------------------------------------- |
| `RUUTER_INTERNAL`   | yes            | URL of the internal Ruuter API                                                          |
| `VAULT_ADDR`        | when using LLM | HashiCorp Vault address                                                                 |
| `VAULT_SECRET_PATH` | when using LLM | Vault path for Azure OpenAI credentials                                                 |
| `VAULT_TOKEN_PATH`  | no             | Path to the Vault Agent token file (default: `/agent/out/token`)                      |
| `LANGUAGES`         | no             | JSON array of language codes (default: `["est","rus","eng"]`)                         |
| `SKIP_CLEANUP`      | no             | Set to `true` to preserve working directories after processing (useful for debugging) |

Azure OpenAI credentials (`azure_openai_api_key`, `azure_openai_endpoint`, `azure_openai_deployment`) are fetched from Vault per task to support token rotation.

## Dependencies

- **FastAPI 0.115.12**: Web framework
- **Uvicorn 0.34.2**: ASGI server
- **Trafilatura 2.0.0**: High-precision HTML content extraction
- **BeautifulSoup4 4.13.4**: HTML parsing fallback
- **PyMuPDF 1.25.5 / pymupdf4llm 0.0.17**: PDF to Markdown conversion and image extraction
- **Unstructured 0.18.5**: DOCX, DOC, PPTX parsing
- **python-pptx 1.0.2**: PPTX image extraction
- **python-docx**: DOCX image extraction
- **OpenAI 1.82.0**: Azure OpenAI client
- **langdetect 1.0.9**: Language detection
- **Markdownify 0.14.1**: HTML to Markdown conversion
- **Pydantic Settings 2.10.1**: Configuration management
- **Requests 2.32.3**: HTTP client

## Running the Service

### Development

```bash
pip install -r requirements.txt
uvicorn api.app:app --host 0.0.0.0 --port 8123 --reload
```

### Docker

```bash
docker build -t cleaning .

docker run -p 8123:8123 \
  -e RUUTER_INTERNAL="http://ruuter-internal:8089" \
  -v /scrapped-data:/scrapped-data \
  cleaning
```

## Testing

Tests live in `tests/` at the repo root and are split into three files:

| File | Type | Docker required |
|------|------|-----------------|
| `test_tasks.py` | Unit — extraction logic, routing, metadata mutation | No |
| `test_api.py` | API contract — request validation, HTTP semantics | Yes |
| `test_integration.py` | End-to-end — full pipeline against real containers | Yes |

All commands are run from the **repo root**. `PYTHONPATH=cleaning` is required so the test imports resolve correctly.

### Unit tests (no Docker)

```bash
pip install -r cleaning/requirements.txt
pip install pytest pytest-cov hvac loguru requests

PYTHONPATH=cleaning pytest tests/test_tasks.py -v
```

### Integration tests (requires Docker)

The test stack (`docker-compose-test.yml`) spins up a Vault dev instance, a mock Ruuter stub, and the cleaning service itself. `conftest.py` manages the full lifecycle automatically.

```bash
pip install -r cleaning/requirements.txt
pip install pytest pytest-timeout hvac loguru requests

mkdir -p test-vault/agent-out test-scrapped-data
docker compose -f docker-compose-test.yml build cleaning-server-test

PYTHONPATH=cleaning pytest tests/test_api.py tests/test_integration.py -v --timeout=300
```

### LLM tests

Tests that exercise the `use_llm` and `use_llm_correction` paths are automatically **skipped** when Azure credentials are absent, so the suite always passes without them. To run the full suite including LLM tests, set the following before running integration tests:

```bash
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_ENDPOINT=...
export AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini   # optional, this is the default
export AZURE_OPENAI_API_VERSION=2024-02-01   # optional, this is the default
```

### GitHub Actions

The workflow at [`.github/workflows/test-cleaning.yml`](../.github/workflows/test-cleaning.yml) runs automatically on every pull request that touches `cleaning/`, `tests/`, `docker-compose-test.yml`, or `test-vault/`. It runs unit tests first, then integration tests only if unit tests pass. Azure credentials are stored as repository secrets (`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`) — if they are not configured the LLM tests are skipped but everything else runs normally.

## Integration

- **Scrapper service**: sends files for cleaning after scraping
- **Ruuter Internal**: receives status updates and stores cleaned content metadata
- **File storage**: cleaned text and images are uploaded through Ruuter

## Ruuter API Calls

Ruuter integration calls use `POST`, and most use a 30-second timeout. Core pipeline calls (file upload, cleaned-file record update) raise on non-2xx responses so failures surface immediately. Status-update and cleanup calls (`update-scrapped-file-stop-scrapping`, `update-status`, `update-zip-dirty`) are handled on a best-effort basis and log failures instead of raising exceptions. The base URL comes from the `RUUTER_INTERNAL` environment variable.

### Upload file

Used for `cleaned.txt`, `cleaned.meta.json`, images, and the cleaning log.

```
POST /ckb/pipeline/upload-file-sync
{"source_file_path": "/scrapped-data/.../file"}
→ {"response": "<uploaded-file-url>"}
```

### Update cleaned file record

Called once per file after all uploads succeed. Persists the URLs of the cleaned text, metadata, and any extracted images.

```
POST /ckb/source-file/update-cleaned-file
{
  "base_id": "<source_file_id>",
  "cleaned_data_url": "<url>",
  "cleaned_metadata_url": "<url>",
  "image_urls": ["<url>", ...]
}
```

### Update file status (batch only)

Called at the start of each file in a batch job to mark it as being cleaned.

```
POST /ckb/source-file/update-scrapped-file-stop-scrapping
{"base_id": "<source_file_id>", "status": "cleaning"}
```

### Update run report (batch only)

Called after all files in a batch are processed. Includes timestamps, log URL, and file counts.

```
POST /ckb/reports/update
{
  "baseId": "<source_run_report_base_id>",
  "scrapingFinishedAt": "<ISO timestamp>",
  "scrapingLogUrl": "<url>",
  ...
}
```

### Update source status (batch only)

Best-effort call after the batch completes.

```
POST /ckb/source/update-status
{"source_id": "<source_base_id>", "status": "finished"}
```

### Mark agency zip dirty (batch only)

Best-effort call after the batch completes, triggering a zip rebuild.

```
POST /ckb/agency/update-zip-dirty
{"sourceId": "<source_base_id>", "agencyId": "<agency_base_id>"}
```

### Report error

Called on any task exception. Best-effort (10-second timeout, never raises).

```
POST /ckb/reports/logs/add
{
  "url": "<source_url>",
  "scraped_at": "<ISO timestamp>",
  "error_type": "cleaning",
  "error_message": "<exception message>",
  "source_base_id": "<source_base_id>",
  "agency_base_id": "<agency_base_id>",
  "source_run_report_base_id": "<source_run_report_base_id>"
}
```

## Error Handling

- Exceptions are caught per task, logged, and reported to Ruuter via `POST /ckb/reports/logs/add`
- Individual image extraction failures are logged but do not abort the overall cleaning job
- The working directory is deleted **only after all uploads are confirmed** — a failed upload leaves files in place for retry or inspection
- Set `SKIP_CLEANUP=true` to always preserve the working directory (useful in tests)

## Output

For each cleaned file:

- `cleaned.txt`: Markdown-formatted text
- `cleaned.meta.json`: updated metadata with cleaning status and detected language
- Extracted images (if requested): written to `<directory_path>/images/` and uploaded to blob storage
