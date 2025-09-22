# Common Knowledge Base

The Common Knowledge Base (CKB) is a comprehensive data platform that collects, processes, and manages knowledge from public sector websites and APIs. The system automatically scrapes content, cleans it for large language model consumption, and provides structured access through REST APIs.

## Overview

The CKB serves as a critical data pipeline for the Bürokratt AI assistant, ensuring that responses are based on current, accurate information from Estonian public sector sources. The platform handles the complete lifecycle of data from initial collection through processing and storage.

### Key Features

- **Multi-Source Data Collection**: Web scraping, API integration, and manual file uploads
- **Automated Content Updates**: Periodic data refresh with change detection
- **Content Processing**: HTML cleaning and document text extraction for LLM consumption
- **Scalable Architecture**: Microservices-based design for high availability
- **Comprehensive API**: REST endpoints for all data operations
- **Real-time Monitoring**: Processing status tracking and error reporting

## Quick Start

### Prerequisites

- Docker and Docker Compose
- PostgreSQL database
- S3-compatible storage (AWS S3, MinIO, etc.)

### Local Development

```bash
# Clone the repository
git clone https://github.com/buerokratt/Common-Knowledge.git
cd Common-Knowledge

# Start all services
docker-compose up -d

# Access the web interface
open http://localhost:3000

# API available at
curl http://localhost:8080/ckb/agency/all
```

### Environment Setup

Create `.env` file with required configuration:

```env
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/ckb

# Storage
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
S3_ENDPOINT_URL=your_s3_url
S3_BUCKET_NAME=ckb-storage

# Services
RUUTER_INTERNAL=http://ruuter-internal:8089
RUUTER_EXTERNAL=http://ruuter:8080
```

## System Architecture

The CKB consists of multiple interconnected services:

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
│   Web GUI   │    │ External API │    │  Internal API   │
│   (React)   │───▶│   (Ruuter)   │───▶│   (Ruuter)      │
└─────────────┘    └──────────────┘    └─────────────────┘
                          │                      │
                          ▼                      ▼
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
│  Scrapper   │    │   Cleaning   │    │ File Processing │
│  Service    │◀───┤   Service    │◀───┤    Service      │
└─────────────┘    └──────────────┘    └─────────────────┘
        │                 │                      │
        ▼                 ▼                      ▼
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
│ Scheduler   │    │ Data Export  │    │   PostgreSQL    │
│  Service    │    │   Service    │    │   Database      │
└─────────────┘    └──────────────┘    └─────────────────┘
```

For detailed architecture information, see [ARCHITECTURE.md](./ARCHITECTURE.md).

## Services

### Core Services

| Service             | Purpose                                   | Technology          | Port |
| ------------------- | ----------------------------------------- | ------------------- | ---- |
| **GUI**             | Web interface for CKB management          | React/TypeScript    | 3000 |
| **Ruuter External** | Public API with authentication            | Ruuter YAML configs | 8080 |
| **Ruuter Internal** | Internal service communication            | Ruuter YAML configs | 8089 |
| **Resql**           | SQL query engine and database abstraction | SQL with metadata   | -    |
| **Scrapper**        | Web scraping and content extraction       | Python/Scrapy       | 8000 |
| **Cleaning**        | Content cleaning and text extraction      | Python/FastAPI      | 8001 |
| **File Processing** | File upload and storage management        | Python/FastAPI      | 8888 |
| **Scheduler**       | Task scheduling and automation            | Python/FastAPI      | 8003 |
| **Data Export**     | Database export and archival              | Python/FastAPI      | 8002 |

### Supporting Components

- **PostgreSQL**: Primary database for structured data
- **S3 Storage**: Blob storage for files and content
- **Liquibase**: Database schema migrations
- **Celery**: Background task processing

## Data Flow

### ETL Pipeline

1. **Extract**: Collect data from websites, APIs, and uploads
2. **Transform**: Clean content and extract text for LLM consumption
3. **Load**: Store processed data in database and blob storage

For detailed ETL process documentation, see [ETL_PROCESSES.md](./ETL_PROCESSES.md).

### Processing Workflow

```mermaid
sequenceDiagram
    participant User
    participant GUI
    participant Scrapper
    participant Cleaning
    participant Storage
    participant DB

    User->>GUI: Configure data source
    GUI->>Scrapper: Trigger scraping
    Scrapper->>Storage: Store raw content
    Scrapper->>DB: Store metadata
    Scrapper->>Cleaning: Request cleaning
    Cleaning->>Storage: Store cleaned text
    Cleaning->>DB: Update status
```

## Component Documentation

Each service has detailed documentation in its respective directory:

- [Scrapper Service](./scrapper/README.md) - Web scraping and content collection
- [Cleaning Service](./cleaning/README.md) - Content processing and text extraction
- [File Processing Service](./file-processing/README.md) - File upload and storage management
- [Data Export Service](./data-export/README.md) - Database export and archival
- [Scheduler Service](./scheduler/README.md) - Task scheduling and automation
- [External API Configuration](./DSL/Ruuter/ckb/README.md) - Public API endpoints
- [Internal API Configuration](./DSL/Ruuter.internal/ckb/README.md) - Service communication
- [Resql Query Definitions](./DSL/Resql/README.md) - SQL query engine and database operations

## Database Schema

The database uses a multi-schema design organized by functional areas:

### Schema Organization

- **agency_management**: Agency and organizational data
- **data_collection**: Sources and file metadata
- **monitoring**: Processing reports and execution logs

### Core Tables

- **agency**: Organization/department information
- **source**: Data source configurations (websites, APIs)
- **source_file**: Individual file metadata and processing status
- **source_run_report**: Processing execution reports
- **source_run_page**: Detailed scraping logs

For detailed schema documentation and ER diagram, see [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md).

### Migration Management

Database schema is managed through Liquibase:

```bash
# Create new migration
./create-migration.sh "descriptive-migration-name"

# Run migrations
./migrate.sh

# Load test data
./load-test-data.sh
```

#### Migration Scripts

- **`create-migration.sh`**: Creates new Liquibase migration files with proper timestamps

  - Generates SQL migration file (`changelog/YYYYMMDDHHMMSS-name.sql`)
  - Creates rollback file (`changelog/YYYYMMDDHHMMSS-rollback.sql`)
  - Generates Liquibase XML configuration (`changelog/YYYYMMDDHHMMSS-name.xml`)
  - Uses git user.name for author attribution

- **`migrate.sh`**: Executes pending database migrations using Docker
- **`load-test-data.sh`**: Loads test fixtures for development

## Development

### Local Setup

1. **Prerequisites**

   ```bash
   # Install Docker and Docker Compose
   # Ensure PostgreSQL is available
   # Configure AWS/S3 access
   ```

2. **Environment Configuration**

   ```bash
   # Copy example configuration
   cp .env.example .env

   # Edit configuration file
   vim .env
   ```

3. **Database Setup**

   ```bash
   # Run database migrations
   ./migrate.sh

   # Load test data (optional)
   ./load-test-data.sh
   ```

4. **Start Services**

   ```bash
   # Start all services
   docker-compose up -d

   # Or start individual services
   docker-compose up gui scrapper cleaning
   ```

### Testing

```bash
# Run API tests
curl http://localhost:8080/ckb/agency/all

# Test scraping functionality
curl -X POST http://localhost:8000/specified-pages-scrapper-task \
  -H "Content-Type: application/json" \
  -d '{"agency_id": "test", "source_id": "test", "urls": []}'

# Check service health
docker-compose ps
```

### Configuration

The system uses DSL (Domain Specific Language) configurations for:

- **API Endpoints**: YAML definitions in `DSL/Ruuter/` and `DSL/Ruuter.internal/`
- **Database Queries**: SQL definitions with metadata in `DSL/Resql/`
- **Data Mapping**: Transformation templates in `DSL/DMapper/`
- **Data Exports**: Export task definitions in `DSL/Export/`
- **Scheduling**: Cron configurations in `DSL/CronManager/`

### Resql Query Engine

Resql provides type-safe database operations:

- **SQL Separation**: Database logic separated from application code
- **Parameter Binding**: Safe parameterized queries prevent SQL injection
- **Type Validation**: Parameter and response type checking
- **Self-Documentation**: Metadata declarations in SQL files

## Deployment

### Production Deployment

1. **Container Registry**

   ```bash
   # Build and push images
   docker build -t ckb/gui ./GUI
   docker build -t ckb/scrapper ./scrapper
   docker build -t ckb/cleaning ./cleaning
   # ... build other services
   ```

2. **Environment Variables**

   - Configure database connections
   - Set up S3 credentials
   - Define service endpoints
   - Set security keys

3. **Service Orchestration**
   - Deploy using Kubernetes or Docker Swarm
   - Configure load balancers
   - Set up monitoring and logging

### Monitoring

- **Health Checks**: Each service exposes health endpoints
- **Logging**: Centralized logging with structured formats
- **Metrics**: Performance and usage metrics collection
- **Alerts**: Automated alerting for critical issues

## API Usage

### Authentication

```bash
# Login to get JWT token
curl -X POST http://localhost:8080/ckb/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "user", "password": "pass"}'

# Use token in subsequent requests
curl -H "Authorization: Bearer <token>" \
  http://localhost:8080/ckb/agency/all
```

### Common Operations

```bash
# List all agencies
curl http://localhost:8080/ckb/agency/all

# Create new source
curl -X POST http://localhost:8080/ckb/source/add \
  -H "Content-Type: application/json" \
  -d '{"agency_id": "agency1", "name": "Source Name", "url": "https://example.com"}'

# Trigger scraping
curl -X POST http://localhost:8080/ckb/source/refresh \
  -d '{"source_id": "source1"}'

# Check processing status
curl http://localhost:8080/ckb/reports/all
```

## Contributing

### Development Guidelines

1. **Code Standards**: Follow existing code conventions in each service
2. **Testing**: Add tests for new functionality
3. **Documentation**: Update relevant README files
4. **ADR Compliance**: Follow [Architectural Decision Records](https://github.com/buerokratt/Buerokratt-onboarding/tree/main/Architectural-Decision-Records-ADR/data-pipelines)

### Pull Request Process

1. Create feature branch from `main`
2. Implement changes with appropriate tests
3. Update documentation as needed
4. Submit pull request for review
5. Address feedback and merge

### Issue Management

Issues are refined during grooming sessions in collaboration with developers to achieve optimal results. Please provide detailed requirements and use cases when submitting issues.

## Troubleshooting

### Common Issues

1. **Service Connection Errors**

   - Check service health: `docker-compose ps`
   - Verify network connectivity between services
   - Review environment variable configuration

2. **Database Issues**

   - Check PostgreSQL connection
   - Verify migration status
   - Review database logs

3. **Scraping Failures**
   - Check target website availability
   - Review scraper logs for errors
   - Verify authentication credentials

### Log Locations

- **Service Logs**: `docker-compose logs <service_name>`
- **Scraper Logs**: `./scrapped-data/logs/scraper/`
- **Cleaning Logs**: `./scrapped-data/logs/cleaning/`
- **Database Logs**: PostgreSQL container logs

## License

This project is licensed under the terms specified in the [LICENSE](./LICENSE) file.

## Links

- **ADR Requirements**: [Data Pipeline ADRs](https://github.com/buerokratt/Buerokratt-onboarding/tree/main/Architectural-Decision-Records-ADR/data-pipelines)
- **Bürokratt Project**: [Main Bürokratt Repository](https://github.com/buerokratt)
- **Architecture Documentation**: [ARCHITECTURE.md](./ARCHITECTURE.md)
- **ETL Process Documentation**: [ETL_PROCESSES.md](./ETL_PROCESSES.md)
- **API Specifications**: [API_SPECIFICATION.md](./API_SPECIFICATION.md)
- **Database Schema**: [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md)
