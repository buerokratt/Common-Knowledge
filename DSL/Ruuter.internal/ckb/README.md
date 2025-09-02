# Ruuter Internal API Configuration

This directory contains Ruuter configuration files for internal API endpoints that handle backend operations for the Common Knowledge Base. These endpoints are used by microservices for database operations and pipeline coordination.

## Overview

Ruuter Internal provides backend API endpoints that are called by other services within the CKB ecosystem. Unlike the external API, these endpoints do not require user authentication and are designed for service-to-service communication.

## Structure

```
DSL/Ruuter.internal/ckb/
├── GET/                 # Read operations
│   ├── agency/          # Agency data retrieval
│   ├── client/          # Client data operations
│   ├── pipeline/        # Pipeline status and triggers
│   ├── source-file/     # File processing queries
│   └── source/          # Source management
├── POST/                # Write operations
│   ├── agency/          # Agency updates
│   ├── pipeline/        # Pipeline operations
│   ├── reports/         # Report management
│   ├── source-file/     # File processing
│   └── source/          # Source updates
└── TEMPLATES/           # Reusable workflow templates
    ├── agency/          # Agency-specific workflows
    ├── pipeline/        # Data processing workflows
    └── source_file/     # File processing workflows
```

## Key Endpoints

### Pipeline Management
- `GET /pipeline/scheduler-check-for-unscheduled-records` - Find sources needing scheduling
- `GET /pipeline/trigger-pipeline-for-sceduled-sources` - Execute scheduled tasks
- `GET /pipeline/zip` - Archive processed data
- `POST /pipeline/clean-scraped-file` - Trigger file cleaning
- `POST /pipeline/upload-file-sync` - Synchronous file upload
- `POST /pipeline/delete-file-sync` - Synchronous file deletion

### Source File Operations
- `GET /source-file/get-one-source-file-to-scrape` - Get next file for processing
- `POST /source-file/add-scrapped-file` - Add scraped file to database
- `POST /source-file/update-cleaned-file` - Update file after cleaning
- `POST /source-file/update-scrapped-file` - Update scraping status
- `POST /source-file/upload-metadata` - Upload file metadata

### Agency Operations
- `GET /agency/get` - Retrieve agency information
- `POST /agency/update-data-hash` - Update agency data hash
- `POST /agency/update-zip-dirty` - Mark agency data for re-zipping

### Source Management
- `GET /source/get` - Retrieve source configuration
- `POST /source/update-status` - Update source processing status

### Client Data Operations
- `GET /client/data/exist` - Check if client data exists
- `GET /client/data/import` - Import client data
- `GET /client/data/new` - Get new client data

### Report Management
- `POST /reports/add` - Create processing report
- `POST /reports/update` - Update report status
- `POST /reports/logs/add` - Add log entries

## Templates

Reusable workflow templates for common operations:

### Pipeline Templates
- `clean-file.yml` - File cleaning workflow
- `trigger-scrapper-*.yml` - Various scraper trigger workflows
- `update-next-rendered-timestamp.yml` - Scheduling updates

### Agency Templates
- `zip.yml` - Agency data archival workflow

### File Templates
- `upload-metadata.yml` - File metadata upload workflow

### Authentication
- `tara.yml` - TARA authentication integration

## Database Integration

Internal endpoints directly execute SQL queries via Resql integration:
- **Read Operations**: Execute SELECT queries from `DSL/Resql/ckb/GET/`
- **Write Operations**: Execute INSERT/UPDATE/DELETE from `DSL/Resql/ckb/POST/`
- **Transaction Management**: Proper database transaction handling
- **Connection Pooling**: Efficient database connection management

## Service Communication

Internal API endpoints are called by:
- **Scrapper Service**: File processing and metadata updates
- **Cleaning Service**: Cleaned file uploads and status updates
- **Data Export**: Database queries and data extraction
- **Scheduler**: Pipeline trigger coordination
- **File Processing**: File upload/download operations

## Configuration Format

Each YAML file typically contains:
```yaml
DSL:
  - name: operation_name
    http:
      method: GET/POST
      url: /internal/endpoint
    steps:
      - name: step_name
        resql:
          query: query_name
        assign: variable_name
```

## Error Handling

- **Database Errors**: Proper SQL error handling and rollback
- **Service Failures**: Graceful degradation when dependent services are unavailable
- **Validation Errors**: Input validation with appropriate error responses
- **Logging**: Comprehensive logging for debugging and monitoring

## Security

While internal endpoints don't require user authentication:
- **Network Isolation**: Should only be accessible within the service network
- **Input Validation**: All inputs validated before database operations
- **SQL Injection Prevention**: Parameterized queries via Resql
- **Resource Limits**: Rate limiting and resource constraints

## Development

When adding new internal endpoints:
1. Create YAML configuration in appropriate GET/POST directory
2. Define corresponding SQL queries in Resql directory
3. Add error handling and validation
4. Test service integration
5. Update documentation

## Monitoring

- **Health Checks**: Endpoint availability monitoring
- **Performance Metrics**: Query execution time tracking
- **Error Rates**: Failed operation monitoring
- **Resource Usage**: Database connection and memory monitoring