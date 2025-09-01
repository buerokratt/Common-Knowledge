# Common Knowledge Base - API Specification

## Overview

The Common Knowledge Base provides multiple API interfaces for different purposes:
- **External API** (Ruuter): Public endpoints with authentication
- **Internal API** (Ruuter Internal): Service-to-service communication
- **Microservice APIs**: Direct service endpoints for specialized operations

## API Access

### External API (Port 8080)
**Base URL**: `http://localhost:8080/ckb`
**Authentication**: JWT Bearer token required
**Documentation**: Available at `/docs` (OpenAPI/Swagger)

### Service APIs
Each microservice exposes its own API with auto-generated documentation:

| Service | Port | Docs URL | Purpose |
|---------|------|----------|---------|
| **File Processing** | 8888 | `http://localhost:8888/docs` | File operations and storage |
| **Scrapper** | 8000 | `http://localhost:8000/docs` | Web scraping tasks |
| **Cleaning** | 8001 | `http://localhost:8001/docs` | Content cleaning |
| **Scheduler** | 8003 | `http://localhost:8003/docs` | Task scheduling |
| **Data Export** | 8002 | `http://localhost:8002/docs` | Data export operations |
| **Search** | 3000 | - | Content search and indexing |

## Authentication

### Login
```http
POST /ckb/auth/login
Content-Type: application/json

{
  "username": "string",
  "password": "string"
}
```

**Response:**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

### Using JWT Token
```http
Authorization: Bearer eyJ...
```

### User Info
```http
GET /ckb/auth/jwt/userinfo
Authorization: Bearer eyJ...
```

## External API Endpoints

### Agency Management

#### GET /ckb/agency/all
List all agencies.

**Response:**
```json
[
  {
    "id": "uuid",
    "base_id": "uuid", 
    "name": "Agency Name",
    "sector": "Healthcare",
    "external_id": "ext_123",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z",
    "type": "client",
    "data_hash": "sha1_hash"
  }
]
```

#### GET /ckb/agency/get
Get specific agency details.

**Query Parameters:**
- `base_id` (UUID): Agency base ID

#### POST /ckb/agency/add
Create new agency.

**Request Body:**
```json
{
  "name": "string",
  "sector": "string", 
  "external_id": "string"
}
```

#### POST /ckb/agency/edit
Update agency information.

**Request Body:**
```json
{
  "base_id": "uuid",
  "name": "string",
  "sector": "string"
}
```

#### POST /ckb/agency/remove
Delete agency.

**Request Body:**
```json
{
  "base_id": "uuid"
}
```

### Source Management

#### GET /ckb/source/all
List all sources.

**Query Parameters:**
- `agency_base_id` (UUID, optional): Filter by agency

**Response:**
```json
[
  {
    "id": "uuid",
    "base_id": "uuid",
    "agency_base_id": "uuid", 
    "url": "https://example.com",
    "subsector": "Public Health",
    "type": "url_to_scrape",
    "status": "running",
    "update_automatically": true,
    "cron_schedule": "0 */6 * * *",
    "last_scraped_at": "2025-01-01T12:00:00Z",
    "next_scrapping_at": "2025-01-01T18:00:00Z"
  }
]
```

#### GET /ckb/source/api/all
List API sources specifically.

#### GET /ckb/source/get
Get specific source details.

**Query Parameters:**
- `base_id` (UUID): Source base ID

#### POST /ckb/source/add
Create new data source.

**Request Body:**
```json
{
  "agency_base_id": "uuid",
  "url": "https://example.com",
  "subsector": "string",
  "type": "url_to_scrape",
  "update_automatically": true,
  "cron_schedule": "0 */6 * * *"
}
```

#### POST /ckb/source/edit-scrape-interval
Update scraping frequency.

**Request Body:**
```json
{
  "base_id": "uuid",
  "cron_schedule": "0 */12 * * *"
}
```

#### POST /ckb/source/edit-subsector
Update source subsector.

**Request Body:**
```json
{
  "base_id": "uuid", 
  "subsector": "New Subsector"
}
```

#### POST /ckb/source/refresh
Trigger source scraping.

**Request Body:**
```json
{
  "base_id": "uuid"
}
```

#### POST /ckb/source/stop
Stop source processing.

**Request Body:**
```json
{
  "base_id": "uuid"
}
```

#### POST /ckb/source/remove
Delete source.

**Request Body:**
```json
{
  "base_id": "uuid"
}
```

### File Operations

#### GET /ckb/source-file/all
List source files.

**Query Parameters:**
- `agency_base_id` (UUID, optional): Filter by agency
- `source_base_id` (UUID, optional): Filter by source
- `type` (string, optional): Filter by file type

**Response:**
```json
[
  {
    "id": "uuid",
    "base_id": "uuid",
    "source_base_id": "uuid",
    "agency_base_id": "uuid",
    "url": "https://example.com/page",
    "page_title": "Document Title",
    "file_name": "document.pdf",
    "type": "scraped_file",
    "status": "finished",
    "subsector": "Healthcare",
    "is_excluded": false,
    "original_data_url": "s3://bucket/raw/file",
    "cleaned_data_url": "s3://bucket/cleaned/file",
    "created_at": "2025-01-01T00:00:00Z",
    "last_scraped_at": "2025-01-01T12:00:00Z"
  }
]
```

#### POST /ckb/source-file/add-uploaded-files
Add uploaded files to a source.

**Request Body:**
```json
{
  "source_base_id": "uuid",
  "files": [
    {
      "file_name": "document.pdf",
      "original_data_url": "s3://bucket/uploads/file",
      "external_id": "ext_123"
    }
  ]
}
```

#### POST /ckb/source-file/get-upload-urls
Get presigned upload URLs.

**Request Body:**
```json
{
  "source_base_id": "uuid",
  "file_names": ["file1.pdf", "file2.docx"]
}
```

**Response:**
```json
{
  "upload_urls": [
    {
      "file_name": "file1.pdf", 
      "upload_url": "https://s3.amazonaws.com/presigned-url",
      "expires_at": "2025-01-01T01:00:00Z"
    }
  ]
}
```

#### POST /ckb/source-file/exclude
Exclude files from processing.

**Request Body:**
```json
{
  "base_ids": ["uuid1", "uuid2"]
}
```

#### POST /ckb/source-file/edit-file
Edit file metadata.

**Request Body:**
```json
{
  "base_id": "uuid",
  "page_title": "New Title",
  "subsector": "New Subsector"
}
```

#### POST /ckb/source-file/refresh
Trigger file reprocessing.

**Request Body:**
```json
{
  "base_ids": ["uuid1", "uuid2"]
}
```

#### POST /ckb/source-file/remove
Delete files.

**Request Body:**
```json
{
  "base_ids": ["uuid1", "uuid2"]
}
```

### Reports and Monitoring

#### GET /ckb/reports/all
List processing reports.

**Query Parameters:**
- `agency_base_id` (UUID, optional): Filter by agency
- `source_base_id` (UUID, optional): Filter by source

**Response:**
```json
[
  {
    "id": "uuid",
    "base_id": "uuid",
    "agency_base_id": "uuid",
    "source_base_id": "uuid", 
    "agency_name": "Agency Name",
    "url": "https://example.com",
    "scraping_started_at": "2025-01-01T12:00:00Z",
    "scraping_finished_at": "2025-01-01T12:30:00Z",
    "errors": 2,
    "scraping_log_url": "s3://bucket/logs/scraping.log",
    "cleaning_log_url": "s3://bucket/logs/cleaning.log"
  }
]
```

#### GET /ckb/reports/logs/all
Get detailed processing logs.

#### POST /ckb/reports/remove
Delete processing reports.

**Request Body:**
```json
{
  "base_ids": ["uuid1", "uuid2"] 
}
```

### File Download

#### GET /ckb/get-download-url
Generate download URL for a file.

**Query Parameters:**
- `blob_storage_path` (string): Path to file in blob storage

**Response:**
```json
{
  "download_url": "https://s3.amazonaws.com/presigned-url",
  "expires_at": "2025-01-01T01:00:00Z"
}
```

## Microservice APIs

### File Processing API (Port 8888)

**OpenAPI Docs**: `http://localhost:8888/docs`

#### Upload Operations
- `POST /upload-urls` - Generate presigned upload URLs
- `POST /upload` - Async file upload with task tracking
- `POST /upload-sync` - Synchronous file upload
- `POST /upload-file-content` - Direct content upload
- `GET /upload/{task_id}` - Upload task status

#### Download Operations  
- `POST /download-urls` - Generate download URLs
- `POST /download-files-to-volume` - Download to local storage
- `POST /download-files-to-volume-async` - Async download
- `GET /download-task/{task_id}` - Download task status

#### File Management
- `POST /delete-files` - Delete from blob storage
- `POST /delete-files-async` - Async deletion
- `POST /move-files` - Move files in storage
- `POST /move-files-async` - Async file move
- `POST /zip-and-upload-folders` - Create archives

### Scrapper API (Port 8000)

**OpenAPI Docs**: `http://localhost:8000/docs`

#### Scraping Tasks
- `POST /specified-pages-scrapper-task` - Scrape specific URLs
- `POST /entire-source-scrapper-task` - Comprehensive site scraping
- `POST /sitemap-collect-scrapper-task` - Sitemap-based scraping
- `POST /eesti-scrapper-task` - Estonian government sites
- `POST /specified-api-files-scrapper-task` - Process API files
- `POST /uploaded-file` - Process uploaded files
- `POST /generate-edited-metadata` - Generate file metadata

### Cleaning API (Port 8001)

**OpenAPI Docs**: `http://localhost:8001/docs`

#### Content Processing
- `POST /clean_file` - Clean and extract text from files

### Scheduler API (Port 8003)

**OpenAPI Docs**: `http://localhost:8003/docs`

#### Schedule Management
- `POST /render_next_run` - Calculate next execution time

### Data Export API (Port 8002)

**OpenAPI Docs**: `http://localhost:8002/docs`

#### Export Operations
- `GET /exports` - List available export tasks
- `POST /exports` - Trigger all exports
- `GET /exports/{task_name}` - Get specific export task
- `POST /exports/{task_name}` - Trigger specific export

### Search API (Port 3000)

#### Search Operations
- `POST /index/{sourceId}/bulk` - Bulk index documents
- `GET /search/{sourceId}` - Search within source
- `GET /document/{sourceId}/{documentId}` - Get full document
- `DELETE /index/{sourceId}` - Delete source index
- `DELETE /documents/{sourceId}/{sourceFileId}` - Delete documents
- `GET /stats/{sourceId}` - Get source statistics
- `GET /health` - Service health check

## Error Responses

### Standard Error Format
```json
{
  "error": "Error description",
  "code": "ERROR_CODE", 
  "details": "Additional error details"
}
```

### Common HTTP Status Codes
- **200**: Success
- **400**: Bad Request - Invalid parameters
- **401**: Unauthorized - Missing or invalid authentication
- **403**: Forbidden - Insufficient permissions
- **404**: Not Found - Resource does not exist
- **500**: Internal Server Error - Server processing error

## Data Models

### Agency
```json
{
  "id": "uuid",
  "base_id": "uuid",
  "name": "string",
  "sector": "string",
  "external_id": "string",
  "type": "client | api",
  "zip_dirty": "boolean",
  "is_zipping": "boolean",
  "data_hash": "string",
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### Source
```json
{
  "id": "uuid",
  "base_id": "uuid", 
  "agency_base_id": "uuid",
  "url": "string",
  "subsector": "string",
  "type": "url_to_scrape | file | api",
  "status": "new | running | finished | failed",
  "update_automatically": "boolean",
  "cron_schedule": "string",
  "last_scraped_at": "timestamp",
  "next_scrapping_at": "timestamp",
  "is_stopping": "boolean",
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### Source File
```json
{
  "id": "uuid",
  "base_id": "uuid",
  "source_base_id": "uuid",
  "agency_base_id": "uuid",
  "url": "string",
  "page_title": "string",
  "file_name": "string",
  "type": "scraped_file | uploaded_file | api_file",
  "status": "scraping | cleaning | finished | not_found | failed",
  "subsector": "string",
  "is_excluded": "boolean",
  "original_data_url": "string",
  "cleaned_data_url": "string", 
  "edited_data_url": "string",
  "original_metadata_url": "string",
  "cleaned_metadata_url": "string",
  "edited_metadata_url": "string",
  "original_data_hash": "string",
  "external_id": "string",
  "created_at": "timestamp",
  "updated_at": "timestamp",
  "last_scraped_at": "timestamp",
  "originally_scraped": "timestamp"
}
```

### Source Run Report
```json
{
  "id": "uuid",
  "base_id": "uuid",
  "agency_base_id": "uuid",
  "source_base_id": "uuid",
  "agency_name": "string",
  "url": "string",
  "scraping_started_at": "timestamp",
  "scraping_finished_at": "timestamp", 
  "errors": "integer",
  "scraping_log_url": "string",
  "cleaning_log_url": "string",
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

## API Usage Examples

### Complete Workflow Example

```bash
# 1. Login and get token
TOKEN=$(curl -X POST http://localhost:8080/ckb/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "password"}' \
  | jq -r '.access_token')

# 2. Create agency
AGENCY_RESPONSE=$(curl -X POST http://localhost:8080/ckb/agency/add \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Agency", "sector": "Healthcare"}')

AGENCY_ID=$(echo $AGENCY_RESPONSE | jq -r '.base_id')

# 3. Add data source
SOURCE_RESPONSE=$(curl -X POST http://localhost:8080/ckb/source/add \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"agency_base_id\": \"$AGENCY_ID\", \"url\": \"https://example.com\", \"type\": \"url_to_scrape\"}")

SOURCE_ID=$(echo $SOURCE_RESPONSE | jq -r '.base_id')

# 4. Trigger scraping
curl -X POST http://localhost:8080/ckb/source/refresh \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"base_id\": \"$SOURCE_ID\"}"

# 5. Monitor progress
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8080/ckb/reports/all?source_base_id=$SOURCE_ID"

# 6. List collected files
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8080/ckb/source-file/all?source_base_id=$SOURCE_ID"

# 7. Search content (if search service is running)
curl "http://localhost:3000/search/$SOURCE_ID?q=healthcare&page=1&size=10"
```

### File Upload Example

```bash
# 1. Get upload URLs
UPLOAD_RESPONSE=$(curl -X POST http://localhost:8080/ckb/source-file/get-upload-urls \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"source_base_id\": \"$SOURCE_ID\", \"file_names\": [\"document.pdf\"]}")

UPLOAD_URL=$(echo $UPLOAD_RESPONSE | jq -r '.upload_urls[0].upload_url')

# 2. Upload file to S3
curl -X PUT "$UPLOAD_URL" \
  -H "Content-Type: application/pdf" \
  --data-binary @document.pdf

# 3. Add file to source
curl -X POST http://localhost:8080/ckb/source-file/add-uploaded-files \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"source_base_id\": \"$SOURCE_ID\", \"files\": [{\"file_name\": \"document.pdf\", \"original_data_url\": \"uploads/path/document.pdf\"}]}"
```

## Rate Limiting

### External API
- **Authentication**: 100 requests/minute per user
- **Data operations**: 1000 requests/hour per user
- **File uploads**: 50 uploads/hour per user

### Internal APIs  
- **Service calls**: No rate limiting (internal network)
- **Resource limits**: Based on infrastructure capacity

## Versioning

### API Versioning Strategy
- **External API**: Versioned via URL path (`/v1/`, `/v2/`)
- **Internal APIs**: Backward compatibility maintained
- **Database Schema**: Liquibase migration versioning
- **Service APIs**: Semantic versioning in OpenAPI specs

### Breaking Changes
- **External API**: New version for breaking changes
- **Internal APIs**: Gradual migration approach
- **Database**: Liquibase rollback support
- **Client Libraries**: Version compatibility matrix

## Security

### Authentication
- **JWT Tokens**: RS256 signature algorithm
- **Token Expiry**: 1 hour default, refresh tokens available
- **Scopes**: Role-based access control

### Authorization
- **User Roles**: Admin, operator, viewer
- **Resource Access**: Agency-based data isolation
- **API Permissions**: Endpoint-level access control

### Data Protection
- **HTTPS Only**: All external communications encrypted
- **Parameter Validation**: Input sanitization and validation
- **SQL Injection Prevention**: Parameterized queries via Resql
- **File Security**: Presigned URLs with expiration

## OpenAPI Documentation

### Accessing API Documentation

Each FastAPI service automatically generates interactive API documentation:

```bash
# File Processing API
open http://localhost:8888/docs

# Scrapper API  
open http://localhost:8000/docs

# Cleaning API
open http://localhost:8001/docs

# Scheduler API
open http://localhost:8003/docs

# Data Export API
open http://localhost:8002/docs
```

### Features
- **Interactive Testing**: Test endpoints directly in browser
- **Request/Response Examples**: Complete API examples
- **Schema Validation**: Parameter and response validation
- **Authentication Testing**: JWT token integration

## Integration Patterns

### Service Communication
- **External → Internal**: User operations trigger internal workflows
- **Internal → Services**: Pipeline coordination via service APIs
- **Service → Service**: Direct API calls for processing workflows
- **Database**: All data access via Resql query engine

### Error Handling
- **Graceful Degradation**: Continue processing when non-critical services fail
- **Retry Logic**: Automatic retry for transient failures
- **Circuit Breakers**: Prevent cascade failures
- **Error Propagation**: Proper error context preservation