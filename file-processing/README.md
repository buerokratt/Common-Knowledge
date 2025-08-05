# File Processing API

A FastAPI application for uploading files to blob storage with background task processing and download URL generation.

## Features

- File upload to S3 with background task processing
- Signed download URL generation
- Provider-agnostic blob storage interface
- Modular architecture with proper separation of concerns

## Project Structure

```
file-processing/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app instance and startup
│   │   ├── __init__.py
│   │   ├── config.py           # Settings/configuration
│   │   └── security.py         # Auth/JWT utilities
│   ├── api/
│   │   ├── __init__.py
│   │   ├── api.py              # Main API router
│   │   ├── upload.py           # Upload endpoints
│   │   ├── tasks.py            # Task status endpoints
│   │   └── download.py         # Download endpoints
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── upload_task.py      # Pydantic models for requests/responses
│   ├── services/
│   │   ├── __init__.py
│   │   ├── blob_storage.py     # Blob storage service
│   │   ├── task_service.py     # Task management service
│   │   └── background_tasks.py # Background task definitions
│   ├── utils/
│   │   └── __init__.py
│   └── tests/
│       └── __init__.py
├── requirements.txt
└── README.md
```

## Environment Variables

- `AWS_ACCESS_KEY_ID`: AWS access key
- `AWS_SECRET_ACCESS_KEY`: AWS secret key
- `AWS_REGION`: AWS region (default: us-east-1)
- `S3_BUCKET_NAME`: S3 bucket name
- `S3_PRESIGNED_URL_EXPIRATION`: URL expiration time in seconds (default: 3600)
- `SOURCE_PATH`: Source directory path (default: /source).

## API Endpoints

### POST /api/v1/upload

Upload a file to blob storage.

**Request Body:**

```json
{
  "source_file_path": "path/to/file.txt"
}
```

**Response:**

```json
{
  "task_id": "uuid",
  "status": "pending"
}
```

### GET /api/v1/tasks/{task_id}

Get the status of an upload task.

**Response:**

```json
{
  "task_id": "uuid",
  "status": "completed",
  "source_file_path": "path/to/file.txt",
  "blob_storage_path": "s3://bucket/uploads/uuid/file.txt",
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

### POST /api/v1/download

Generate a signed download URL for a file.

**Request Body:**

```json
{
  "blob_storage_path": "uploads/uuid/file.txt"
}
```

**Response:**

```json
{
  "download_url": "https://s3.amazonaws.com/...",
  "expires_at": "2024-01-01T01:00:00"
}
```

## Running the Application

### Development

```bash
# Install dependencies
pip install -r requirements.txt


# Start the application
uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload
```

### Docker

```bash
# Build the image
docker build -t file-processing .

# Run the container
docker run -p 8888:8888 \
  -e DB_URI="postgresql://user:pass@host/dbname" \
  -e AWS_ACCESS_KEY_ID="your_key" \
  -e AWS_SECRET_ACCESS_KEY="your_secret" \
  -e S3_BUCKET_NAME="your_bucket" \
  file-processing
```

## Development

### Code Structure

- **Schemas**: Pydantic models for API requests/responses in `app/schemas/`
- **Services**: Business logic in `app/services/`
- **API**: FastAPI routes in `app/api/`
- **Core**: Configuration and database setup in `app/core/`
