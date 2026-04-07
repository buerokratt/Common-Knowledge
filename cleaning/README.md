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
  "base_url": "https://source-url.com",
  "logs_path": "/scrapped-data/.../logfile.log",
  "use_llm": false,
  "use_llm_correction": false,
  "extract_images": false,
  "images_path": "/scrapped-data/.../images/"
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
| `LANGUAGES`         | no             | Comma-separated language codes (default:`est,rus,eng`)                                |
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
uvicorn api.app:app --host 0.0.0.0 --port 8001 --reload
```

### Docker

```bash
docker build -t cleaning .

docker run -p 8123:8123 \
  -e RUUTER_INTERNAL="http://ruuter-internal:8080" \
  -v /scrapped-data:/scrapped-data \
  cleaning
```

## Integration

- **Scrapper service**: sends files for cleaning after scraping
- **Ruuter Internal**: receives status updates and stores cleaned content metadata
- **File storage**: cleaned text and images are uploaded through Ruuter

## Ruuter API Calls

All calls use `POST`, a 30-second timeout, and raise on non-2xx responses. The base URL comes from the `RUUTER_INTERNAL` environment variable.

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

- `cleaned.txt`: plain text content
- `cleaned.meta.json`: updated metadata with cleaning status, language, and timestamps
- Extracted images (if requested): written to `images_path`
