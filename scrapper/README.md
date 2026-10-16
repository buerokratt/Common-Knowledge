# Scrapper Service

A web scraping service built with Scrapy and FastAPI that collects data from various sources including websites, APIs, and uploaded files. The service supports multiple scraping strategies and processes data for the Common Knowledge Base.

## Overview

The scrapper service is responsible for:
- Scraping content from websites and APIs
- Processing uploaded files 
- Generating metadata for scraped content
- Handling different content types (HTML, documents, etc.)
- Providing background task processing via Celery

## Architecture

```
scrapper/
├── api/                    # FastAPI application
│   ├── app.py             # Main API endpoints
│   ├── models.py          # Pydantic models for API requests
│   ├── config.py          # Configuration settings
│   └── utils.py           # Utility functions
├── scrapper/              # Scrapy project
│   ├── spiders/           # Spider implementations
│   │   ├── base_spider.py         # Base spider class
│   │   ├── eesti_spider.py        # Estonian government sites
│   │   ├── entire_source_spider.py # Full website scraping
│   │   ├── sitemap_collect_spider.py # Sitemap-based scraping
│   │   ├── specified_api_files_spider.py # API file scraping
│   │   ├── specified_pages_spider.py # Specific page scraping
│   │   └── uploaded_file_spider.py # Uploaded file processing
│   ├── items.py           # Scrapy item definitions
│   ├── pipelines.py       # Data processing pipelines
│   ├── middlewares.py     # Custom middlewares
│   ├── settings.py        # Scrapy settings
│   └── utils.py           # Utility functions
├── worker/                # Celery worker tasks
│   ├── tasks.py           # Background task definitions
│   └── utils.py           # Worker utilities
└── run_*.py              # Individual scraper execution scripts
```

## Features

### Scraping Types

1. **Specified Pages**: Scrape specific URLs provided in a list
2. **Entire Source**: Comprehensive scraping of entire websites
3. **Sitemap Collection**: Discover and scrape URLs from sitemaps
4. **Estonian Government**: Specialized scraper for Estonian public sector sites
5. **API Files**: Process files from API endpoints
6. **Uploaded Files**: Process manually uploaded files

### Content Processing

- **Multi-format Support**: HTML, documents, images, and other file types
- **Metadata Generation**: Automatic extraction of titles, content type, and timestamps
- **Content Hashing**: SHA1 hashing for duplicate detection
- **Text Extraction**: Clean text extraction from HTML content using BeautifulSoup

## API Endpoints

### POST /specified-pages-scrapper-task
Trigger scraping of specific URLs.

**Request Body:**
```json
{
  "agency_id": "string",
  "source_id": "string", 
  "urls": [
    {
      "url": "https://example.com",
      "id": "string",
      "hash": "string"
    }
  ]
}
```

### POST /entire-source-scrapper-task
Trigger comprehensive website scraping.

**Request Body:**
```json
{
  "agency_id": "string",
  "source_id": "string"
}
```

### POST /sitemap-collect-scrapper-task
Trigger sitemap-based URL discovery and scraping.

**Request Body:**
```json
{
  "agency_id": "string",
  "source_id": "string",
  "url": "https://example.com/sitemap.xml"
}
```

### POST /eesti-scrapper-task
Trigger specialized Estonian government site scraping.

**Request Body:**
```json
{
  "agency_id": "string",
  "source_id": "string"
}
```

### POST /specified-api-files-scrapper-task
Trigger API file processing.

**Request Body:**
```json
{
  "agency_id": "string",
  "source_id": "string",
  "api_files": [
    {
      "id": "string",
      "hash": "string", 
      "externalId": "string"
    }
  ]
}
```

### POST /uploaded-file
Process uploaded files by converting them to scraping tasks.

**Request Body:**
```json
{
  "agency_id": "string",
  "source_id": "string",
  "download_files": [
    {
      "path": "string",
      "download_url": "https://example.com/file.pdf"
    }
  ],
  "urls": [
    {
      "url": null,
      "id": "string",
      "hash": "string",
      "path": "string"
    }
  ]
}
```

### POST /generate-edited-metadata
Generate metadata for edited files.

**Request Body:**
```json
{
  "download_url": "string",
  "source_file_id": "string", 
  "source_file_path": "string"
}
```

## Environment Variables

- `RUUTER_INTERNAL`: Internal Ruuter service URL for database operations
- `SCRAPY_SETTINGS_MODULE`: Scrapy settings module (default: scrapper.settings)
- `CELERY_BROKER_URL`: Celery broker URL for task queue
- `CELERY_RESULT_BACKEND`: Celery result backend URL

## Dependencies

- **Scrapy 2.13.2**: Web scraping framework
- **FastAPI 0.115.12**: Modern web framework for API endpoints
- **Celery 5.5.3**: Distributed task queue for background processing
- **Playwright**: Browser automation for JavaScript-heavy sites
- **BeautifulSoup4**: HTML parsing and text extraction
- **Pydantic**: Data validation and settings management

## Running the Service

### Development

```bash
# Install dependencies
pip install -r requirements.txt

# Start the FastAPI server
uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload

# Start Celery worker (in separate terminal)
celery -A worker.tasks worker --loglevel=info
```

### Docker

```bash
# Build the image
docker build -t scrapper .

# Run the container
docker run -p 8000:8000 \
  -e RUUTER_INTERNAL="http://ruuter-internal:8080" \
  -e CELERY_BROKER_URL="redis://redis:6379/0" \
  scrapper
```

### Individual Scrapers

The service also provides standalone scripts for running specific scrapers:

```bash
python run_eesti_scrapper.py
python run_entire_source_scrapper.py  
python run_sitemap_collect_scrapper.py
python run_specified_api_files_scrapper.py
python run_specified_links_scrapper.py
python run_uploaded_file.py
```

## Integration

The scrapper integrates with:
- **Ruuter Internal**: For database operations and status updates
- **File Processing**: For file storage and metadata management
- **Cleaning Service**: For post-processing scraped content
- **Scheduler**: For automated periodic scraping

## Error Handling

- Comprehensive error logging to database via `source_run_page` table
- Automatic retry mechanisms for failed requests
- Graceful handling of different HTTP status codes
- Source stopping detection to halt scraping when requested

## Data Flow

1. API receives scraping task request
2. Task is queued via Celery for background processing
3. Appropriate spider is selected based on task type
4. Spider scrapes content using Playwright/Scrapy
5. Content is processed and metadata extracted
6. Data is stored via Ruuter Internal API calls
7. Status updates are logged to database

## Configuration

Key configuration options in `scrapper/settings.py`:
- User agent rotation
- Request delays and concurrency limits
- Download timeouts
- Pipeline configurations
- Database connection settings