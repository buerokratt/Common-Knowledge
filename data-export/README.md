# Data Export Service

A FastAPI service that exports data from the Common Knowledge Base database to compressed CSV files. The service supports both bulk exports and individual task exports with automatic data archival and cleanup.

## Overview

The data export service is responsible for:
- Exporting database records to compressed CSV files
- Managing data retention and cleanup
- Providing both manual and automated export capabilities
- Archiving exported data with timestamp-based naming
- Supporting multiple export tasks for different data types

## Architecture

```
data-export/
├── app/                  # FastAPI application
│   ├── main.py          # API endpoints and FastAPI app
│   ├── dsl.py           # Export logic and task management
│   ├── models.py        # Pydantic models
│   ├── db.py            # Database connection utilities
│   ├── config.py        # Configuration settings
│   └── __init__.py
├── debug.py             # Debug utilities
├── requirements.txt     # Python dependencies
└── Dockerfile          # Container configuration
```

## Features

### Export Capabilities

1. **Bulk Export**: Export all configured data types in a single operation
2. **Selective Export**: Export specific data types individually
3. **Compressed Output**: All exports are gzip-compressed for efficient storage
4. **Timestamped Files**: Automatic file naming with export timestamps
5. **Data Cleanup**: Automatic deletion of exported records from database

### Export Tasks

The service discovers export tasks from the DSL directory structure:
- **Agency**: Export agency data and metadata
- **Source**: Export source configuration and status
- **Source File**: Export file metadata and processing status
- **Source Run Page**: Export scraping execution logs
- **Source Run Report**: Export detailed processing reports

## API Endpoints

### POST /exports
Trigger export of all configured data types.

**Response:**
```json
{
  "status": "Ok"
}
```

### GET /exports
List all available export tasks.

**Response:**
```json
[
  {
    "name": "agency",
    "select_query": "COPY (...) TO STDOUT WITH CSV HEADER",
    "delete_query": "DELETE FROM ..."
  }
]
```

### GET /exports/{task_name}
Get details for a specific export task.

**Response:**
```json
{
  "name": "agency",
  "select_query": "COPY (...) TO STDOUT WITH CSV HEADER", 
  "delete_query": "DELETE FROM ..."
}
```

### POST /exports/{task_name}
Trigger export for a specific data type.

**Response:**
```json
{
  "status": "Ok"
}
```

## Data Models

### ExportTask
```python
{
  "name": "str",           # Task name (matches directory name)
  "select_query": "str",   # SQL query for data export
  "delete_query": "str"    # SQL query for data cleanup
}
```

### OkResponse
```python
{
  "status": "str"          # Status message (default: "Ok")
}
```

## Environment Variables

- `DATABASE_URL`: PostgreSQL connection string
- `DSL_PATH`: Path to DSL export definitions (default: /DSL/Export)
- `EXPORT_PATH`: Path for generated export files (default: /exported-data)

## Dependencies

- **FastAPI 0.115.12**: Web framework for API endpoints
- **psycopg2-binary 2.9.10**: PostgreSQL database adapter
- **Pydantic Settings 2.9.1**: Configuration management

## Export Process

1. **Task Discovery**: Scans DSL/Export directory for export task definitions
2. **Query Loading**: Loads SELECT and DELETE SQL queries from task directories
3. **Data Export**: Uses PostgreSQL COPY command for efficient CSV generation
4. **Compression**: Applies gzip compression to reduce file size
5. **File Naming**: Generates timestamped filenames (e.g., `agency-2025_01_01-12_00_00_AM.csv.gz`)
6. **Data Cleanup**: Executes DELETE queries to remove exported records
7. **Transaction Management**: Ensures data consistency with proper commit handling

## File Structure

Each export task requires a directory under `DSL/Export/` containing:
- `select.sql`: Query to export data using COPY TO STDOUT
- `delete.sql`: Query to remove exported records

Example:
```
DSL/Export/
├── agency/
│   ├── select.sql
│   └── delete.sql
├── source/
│   ├── select.sql  
│   └── delete.sql
└── source_file/
    ├── select.sql
    └── delete.sql
```

## Running the Service

### Development

```bash
# Install dependencies
pip install -r requirements.txt

# Start the FastAPI server
uvicorn app.main:app --host 0.0.0.0 --port 8002 --reload
```

### Docker

```bash
# Build the image
docker build -t data-export .

# Run the container
docker run -p 8002:8002 \
  -e DATABASE_URL="postgresql://user:pass@host/dbname" \
  -v /exported-data:/exported-data \
  -v /DSL/Export:/DSL/Export \
  data-export
```

## Integration

The data export service integrates with:
- **Database**: Direct PostgreSQL connection for data export
- **Scheduler**: Automated periodic exports via cron jobs
- **File System**: Local storage for export file generation
- **Monitoring**: Logging for export tracking and debugging

## Export Scheduling

Exports can be scheduled using:
- **Manual Triggers**: Direct API calls for immediate exports
- **Cron Jobs**: Automated periodic exports (configured externally)
- **Pipeline Integration**: Triggered as part of data processing workflows

## Monitoring

- **Logging**: Comprehensive logging of export operations
- **Error Handling**: Graceful handling of database and file system errors
- **Status Tracking**: API endpoints for monitoring export task status
- **File Management**: Automatic cleanup and organization of export files