# Cleaning Service

A FastAPI service that processes and cleans scraped content to make it suitable for use as a knowledge base for large language models. The service removes HTML tags, extracts text content, and normalizes various document formats.

## Overview

The cleaning service is responsible for:
- Cleaning HTML content by removing tags and extracting readable text
- Processing various document formats (PDF, DOCX, DOC, etc.)
- Generating cleaned text files for LLM consumption
- Updating metadata to track cleaning status
- Logging cleaning operations for monitoring

## Architecture

```
cleaning/
├── api/                   # FastAPI application
│   ├── app.py            # Main API endpoints
│   ├── models.py         # Pydantic models
│   ├── config.py         # Configuration settings
│   └── __init__.py
├── worker/               # Processing workers
│   ├── tasks.py          # Cleaning task implementations
│   ├── utils.py          # Utility functions
│   └── __init__.py
├── debug.py             # Debug utilities
├── requirements.txt     # Python dependencies
└── Dockerfile          # Container configuration
```

## Features

### Content Cleaning

1. **HTML Cleaning**: Removes HTML tags, CSS styles, and JavaScript while preserving text content
2. **Document Processing**: Handles PDF, DOCX, DOC, and other document formats using Unstructured library
3. **Text Extraction**: Converts complex documents into clean, readable text
4. **Metadata Preservation**: Maintains original metadata while adding cleaning status

### Processing Capabilities

- **Multi-format Support**: HTML, PDF, DOCX, DOC, and other document types
- **Language Support**: Configurable language processing for international content
- **Error Handling**: Comprehensive error logging and recovery
- **File Management**: Temporary file handling and cleanup

## API Endpoints

### POST /clean_file
Process and clean a file, extracting readable text content.

**Request Body:**
```json
{
  "file_path": "/path/to/file.html",
  "meta_data_path": "/path/to/file.meta.json",
  "directory_path": "/path/to/directory",
  "source_file_id": "uuid",
  "url": "https://source-url.com",
  "logs_path": "/path/to/logfile.log"
}
```

**Response:** 
- HTTP 200 on successful processing
- Generates cleaned.txt and cleaned.meta.json files
- Updates database via Ruuter Internal API

## Data Models

### EntityToClean
```python
{
  "file_path": "FilePath",           # Path to original file
  "meta_data_path": "FilePath",      # Path to metadata file
  "directory_path": "DirectoryPath", # Working directory
  "source_file_id": "str",           # Unique file identifier
  "url": "str",                      # Original source URL
  "logs_path": "FilePath"            # Path to log file
}
```

## Environment Variables

- `RUUTER_INTERNAL`: Internal Ruuter service URL for database operations
- `LANGUAGES`: Supported languages for document processing (configurable in settings)

## Dependencies

- **FastAPI 0.115.12**: Web framework for API endpoints
- **Unstructured 0.18.2**: Document processing library with PDF/DOCX support
- **BeautifulSoup4 4.13.4**: HTML parsing and text extraction
- **Pydantic Settings 2.10.1**: Configuration management

## Running the Service

### Development

```bash
# Install dependencies
pip install -r requirements.txt

# Start the FastAPI server
uvicorn api.app:app --host 0.0.0.0 --port 8001 --reload
```

### Docker

```bash
# Build the image
docker build -t cleaning .

# Run the container
docker run -p 8001:8001 \
  -e RUUTER_INTERNAL="http://ruuter-internal:8080" \
  -v /data:/data \
  cleaning
```

## Processing Flow

1. **File Reception**: API receives cleaning request with file paths and metadata
2. **Content Type Detection**: Determines processing strategy based on file type
3. **Text Extraction**: 
   - HTML files: BeautifulSoup for tag removal and text extraction
   - Other files: Unstructured library for document parsing
4. **Text Cleaning**: Removes excess whitespace and formatting artifacts
5. **File Generation**: Creates cleaned.txt with processed content
6. **Metadata Update**: Updates metadata with cleaning status and timestamps
7. **Storage**: Uploads cleaned files via file processing service
8. **Database Update**: Updates source file records via Ruuter Internal

## Integration

The cleaning service integrates with:
- **Scrapper Service**: Receives files to be cleaned after scraping
- **File Processing**: Uploads cleaned content to blob storage
- **Ruuter Internal**: Updates database records with cleaning status
- **Scheduler**: Triggered as part of automated data processing pipeline

## Error Handling

- **Logging**: Comprehensive logging to both files and console
- **Error Recovery**: Graceful handling of file processing errors
- **Status Tracking**: Updates database with error states when processing fails
- **Resource Cleanup**: Proper cleanup of temporary files and resources

## Configuration

Key settings in `api/config.py`:
- Language configuration for document processing
- File path handling
- Database connection settings
- Logging configuration

## Output

For each cleaned file, the service generates:
- `cleaned.txt`: Plain text content suitable for LLM processing
- `cleaned.meta.json`: Updated metadata with cleaning status
- Log entries for monitoring and debugging