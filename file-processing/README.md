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
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py           # Settings/configuration
│   ├── api/
│   │   ├── __init__.py
│   │   └── endpoints/          # API endpoint modules
│   │       ├── __init__.py
│   │       ├── upload.py       # File upload endpoints
│   │       ├── download.py     # Download URL generation
│   │       ├── delete.py       # File deletion endpoints
│   │       ├── move.py         # File movement operations
│   │       └── zip.py          # Archive creation endpoints
│   ├── schemas/
│   │   └── __init__.py         # Pydantic models for requests/responses
│   ├── services/
│   │   ├── __init__.py
│   │   ├── blob_storage.py     # Blob storage interface
│   │   ├── s3_provider.py      # S3 implementation
│   │   ├── upload_service.py   # Upload operations
│   │   ├── download_service.py # Download operations
│   │   ├── delete_service.py   # Deletion operations
│   │   ├── move_service.py     # File movement
│   │   ├── zip_service.py      # Archive creation
│   │   └── background_tasks.py # Async task processing
│   └── tests/
│       └── __init__.py
├── requirements.txt
├── Dockerfile
└── README.md
```

## Environment Variables

- `AWS_ACCESS_KEY_ID`: AWS access key
- `AWS_SECRET_ACCESS_KEY`: AWS secret key
- `AWS_REGION`: AWS region (default: us-east-1)
- `S3_ENDPOINT_URL`: S3 endpoint url
- `S3_BUCKET_NAME`: S3 bucket name
- `S3_PRESIGNED_URL_EXPIRATION`: URL expiration time in seconds (default: 3600)
- `SOURCE_PATH`: Source directory path (default: /source).

## API Endpoints

### Upload Operations

#### POST /upload-urls

Generate presigned upload URLs for multiple blob paths.

**Request Body:**

```json
{
  "blob_paths": ["path1/file1.txt", "path2/file2.pdf"]
}
```

#### POST /upload

Upload a file with background task tracking.

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

#### POST /upload-sync

Upload a file synchronously.

**Request Body:**

```json
{
  "source_file_path": "path/to/file.txt"
}
```

**Response:**

```json
{
  "blob_storage_path": "uploads/uuid/file.txt",
  "source_file_path": "cleaned/path",
  "status": "completed"
}
```

#### POST /upload-file-content

Upload file content directly to blob storage.

**Request Body:**

```json
{
  "content": "file content as string",
  "blob_path": "path/to/destination.txt",
  "content_type": "text/plain"
}
```

#### GET /upload/{task_id}

Get the status of an upload task.

**Response:**

```json
{
  "task_id": "uuid",
  "status": "completed",
  "source_file_path": "path/to/file.txt",
  "blob_storage_path": "uploads/uuid/file.txt",
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

#### GET /tasks/stats

Get statistics about upload tasks in memory.

#### DELETE /tasks/cleanup

Clean up old tasks from memory (default: 24 hours).

### Download Operations

#### POST /download-urls

Generate presigned download URLs for multiple files.

**Request Body:**

```json
{
  "paths": ["path1/file1.txt", "path2/file2.pdf"]
}
```

**Response:**

```json
{
  "download_urls": [
    {
      "path": "path1/file1.txt",
      "download_url": "https://s3.amazonaws.com/...",
      "expires_at": "2024-01-01T01:00:00"
    }
  ]
}
```

#### POST /download-files-to-volume

Download multiple files from blob storage to local volume (synchronous).

**Request Body:**

```json
{
  "blob_paths": ["path1/file1.txt"],
  "local_base_path": "/local/destination"
}
```

#### POST /download-files-to-volume-async

Start background download of multiple files to local volume.

**Response:**

```json
{
  "task_id": "uuid",
  "status": "pending"
}
```

#### GET /download-task/{task_id}

Get the status of a download task.

#### POST /delete-files-from-volume

Delete multiple files from local volume.

### File Management Operations

#### POST /delete-files

Delete multiple files/folders from blob storage (synchronous).

**Request Body:**

```json
{
  "paths": ["path1/file1.txt", "folder2/"]
}
```

#### POST /delete-files-async

Start background deletion of multiple files/folders.

#### GET /delete-task/{task_id}

Get the status of a delete task.

#### POST /move-files

Move multiple files in blob storage (synchronous).

**Request Body:**

```json
{
  "operations": [
    {
      "source_path": "old/path/file.txt",
      "destination_path": "new/path/file.txt"
    }
  ]
}
```

#### POST /move-files-async

Start background move of multiple files.

#### GET /move-task/{task_id}

Get the status of a move task.

### Archive Operations

#### POST /zip-and-upload-folders

Zip folders from S3 and upload as zip files (synchronous).

**Request Body:**

```json
{
  "folders": ["folder1/", "folder2/"],
  "zip_destination": "archives/combined.zip"
}
```

#### POST /zip-and-upload-folders-async

Start background zipping and uploading of folders.

#### GET /zip-task/{task_id}

Get the status of a zip task.

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
  -e S3_ENDPOINT_URL="your_s3_url" \
  -e S3_BUCKET_NAME="your_bucket" \
  file-processing
```

## Development

### Code Structure

- **Schemas**: Pydantic models for API requests/responses in `app/schemas/`
- **Services**: Business logic in `app/services/`
- **API**: FastAPI routes in `app/api/`
- **Core**: Configuration and database setup in `app/core/`
