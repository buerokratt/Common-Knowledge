# Ruuter External API Configuration

This directory contains Ruuter configuration files for the external-facing API endpoints of the Common Knowledge Base. These endpoints provide secure access to CKB functionality for authenticated users.

## Overview

Ruuter is a lightweight HTTP service router that handles API requests by executing predefined workflows defined in YAML files. This external API layer provides:
- User authentication and authorization
- Agency and source management
- File upload and processing
- Data export and reporting
- Secure access to internal services

## Structure

```
DSL/Ruuter/ckb/
├── GET/                 # Read operations
│   ├── accounts/        # User account management
│   ├── agency/          # Agency data retrieval
│   ├── auth/            # Authentication endpoints
│   ├── reports/         # Report generation
│   ├── source-file/     # File metadata access
│   └── source/          # Source configuration access
├── POST/                # Write operations  
│   ├── agency/          # Agency CRUD operations
│   ├── auth/            # Login/logout
│   ├── reports/         # Report management
│   ├── source-file/     # File operations
│   └── source/          # Source management
└── TEMPLATES/           # Reusable workflow templates
    ├── pipeline/        # Data processing triggers
    ├── source_file/     # File processing workflows
    └── check-user-authority.yml
```

## Key Endpoints

### Authentication
- `POST /auth/login` - User authentication
- `GET /accounts/logout` - User logout
- `GET /auth/jwt/userinfo` - JWT token validation

### Agency Management
- `GET /agency/all` - List all agencies
- `GET /agency/get` - Get specific agency details
- `POST /agency/add` - Create new agency
- `POST /agency/edit` - Update agency information
- `POST /agency/remove` - Delete agency

### Source Management
- `GET /source/all` - List all sources
- `GET /source/api/all` - List API sources
- `POST /source/add` - Create new source
- `POST /source/edit-scrape-interval` - Update scraping frequency
- `POST /source/refresh` - Trigger source refresh
- `POST /source/stop` - Stop source processing

### File Operations
- `GET /source-file/all` - List source files
- `POST /source-file/add-uploaded-files` - Add uploaded files
- `POST /source-file/get-upload-urls` - Get presigned upload URLs
- `POST /source-file/exclude` - Exclude files from processing
- `POST /source-file/remove` - Delete source files

### Reports and Monitoring
- `GET /reports/all` - List processing reports
- `GET /reports/logs/all` - Get processing logs
- `POST /reports/remove` - Delete reports

## Security

- **Authentication Required**: All endpoints require valid JWT tokens
- **Authorization Checks**: User permissions validated via `check-user-authority.yml`
- **Role-based Access**: Different access levels for different user types
- **Secure File Handling**: Presigned URLs for secure file uploads/downloads

## Configuration

Each YAML file defines:
- **Request Validation**: Input parameter validation
- **Database Queries**: SQL operations via Resql integration
- **Response Formatting**: Output data transformation
- **Error Handling**: Error response generation
- **Security Checks**: Authorization and authentication

## Integration

The external Ruuter API integrates with:
- **Ruuter Internal**: Internal service calls for backend operations
- **Database**: Direct database access via Resql queries
- **Authentication Service**: JWT token validation
- **File Processing**: Upload and download operations
- **Scrapper Service**: Triggering scraping operations

## Templates

Reusable templates in `TEMPLATES/` directory:
- **Pipeline Triggers**: Automated workflow initiation
- **File Processing**: Common file operation patterns
- **Authentication**: User authorization checks
- **Data Processing**: ETL pipeline coordination

## Usage

These configurations are loaded by Ruuter service at runtime to provide REST API functionality. Each YAML file represents an API endpoint with defined request/response handling logic.

## Development

When adding new endpoints:
1. Create YAML file in appropriate GET/POST directory
2. Define request validation and response formatting
3. Include authorization checks using templates
4. Test endpoint functionality
5. Update API documentation