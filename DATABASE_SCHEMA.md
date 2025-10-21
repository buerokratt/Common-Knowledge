# Database Schema - Entity Relationship Diagram

## Overview

The Common Knowledge Base uses PostgreSQL with a multi-schema design organized by functional areas. The database supports data collection, processing tracking, and monitoring across the ETL pipeline.

## ER Diagram

```mermaid
erDiagram

    %% Agency Management Schema
    agency {
        UUID id PK "Primary key"
        UUID base_id UK "Business key identifier"
        TEXT name "Agency name"
        TEXT sector "Government sector"
        TEXT external_id "External system ID"
        TIMESTAMP created_at "Creation timestamp"
        TIMESTAMP updated_at "Last update timestamp"
        BOOLEAN is_deleted "Soft delete flag"
        TEXT zipped_data_url "Archived data location"
        BOOLEAN zip_dirty "Needs re-archiving"
        BOOLEAN is_zipping "Currently archiving"
        agency_type type "client or api"
        TEXT data_hash "Content fingerprint"
    }

    %% Data Collection Schema  
    source {
        UUID id PK "Primary key"
        UUID base_id UK "Business key identifier"
        UUID agency_base_id FK "Reference to agency"
        TEXT url "Source URL"
        TEXT subsector "Data subsector"
        TIMESTAMP created_at "Creation timestamp"
        TIMESTAMP updated_at "Last update timestamp"
        TIMESTAMP last_scraped_at "Last scraping time"
        TIMESTAMP next_scrapping_at "Next scheduled scraping"
        BOOLEAN is_deleted "Soft delete flag"
        BOOLEAN is_stopping "Stop processing flag"
        TEXT created_by "Creator identifier"
        source_type type "url_to_scrape, file, or api"
        source_status_type status "new, running, finished, failed"
        BOOLEAN update_automatically "Auto-update enabled"
        TEXT cron_schedule "Scheduling expression"
    }

    source_file {
        UUID id PK "Primary key"
        UUID base_id UK "Business key identifier"
        UUID source_base_id FK "Reference to source"
        UUID agency_base_id FK "Reference to agency"
        TEXT url "Original file URL"
        TEXT page_title "Page/document title"
        TEXT original_data_url "Raw content location"
        TEXT cleaned_data_url "Processed content location"
        TEXT edited_data_url "Manually edited content"
        TEXT original_metadata_url "Raw metadata location"
        TEXT cleaned_metadata_url "Processed metadata location"
        TEXT edited_metadata_url "Edited metadata location"
        TEXT original_data_hash "Content fingerprint"
        TIMESTAMP created_at "Creation timestamp"
        TIMESTAMP updated_at "Last update timestamp"
        TIMESTAMP last_scraped_at "Last scraping time"
        TIMESTAMP originally_scraped "First scraping time"
        source_file_status_type status "Processing status"
        source_file_type type "scraped_file, uploaded_file, api_file"
        TEXT file_name "Original filename"
        TEXT external_id "External system ID"
        TEXT subsector "Data subsector"
        BIGINT file_size "File size in bytes"
        TEXT uploaded_by "User ID who uploaded (uploaded_file only)"
        BOOLEAN is_excluded "Excluded from processing"
        BOOLEAN is_deleted "Soft delete flag"
    }

    %% Monitoring Schema
    source_run_report {
        UUID id PK "Primary key"
        UUID base_id UK "Business key identifier"
        UUID agency_base_id FK "Reference to agency"
        UUID source_base_id FK "Reference to source"
        TEXT agency_name "Agency name snapshot"
        TEXT url "Source URL snapshot"
        TIMESTAMP created_at "Creation timestamp"
        TIMESTAMP updated_at "Last update timestamp"
        TIMESTAMP scraping_started_at "Scraping start time"
        TIMESTAMP scraping_finished_at "Scraping end time"
        INTEGER errors "Error count"
        TEXT scraping_log_url "Scraping log location"
        TEXT cleaning_log_url "Cleaning log location"
        BOOLEAN is_deleted "Soft delete flag"
    }

    source_run_page {
        UUID id PK "Primary key"
        UUID base_id UK "Business key identifier"
        UUID agency_base_id FK "Reference to agency"
        UUID source_base_id FK "Reference to source"
        UUID source_run_report_base_id FK "Reference to report"
        TEXT url "Page URL"
        TIMESTAMP created_at "Creation timestamp"
        TIMESTAMP updated_at "Last update timestamp"
        TIMESTAMP scraped_at "Page scraping time"
        TEXT error_type "Error classification"
        TEXT error_message "Error details"
        BOOLEAN is_deleted "Soft delete flag"
    }

    %% Relationships
    agency ||--o{ source : "agency_base_id"
    agency ||--o{ source_file : "agency_base_id"
    agency ||--o{ source_run_report : "agency_base_id"
    agency ||--o{ source_run_page : "agency_base_id"
    
    source ||--o{ source_file : "source_base_id"
    source ||--o{ source_run_report : "source_base_id"
    source ||--o{ source_run_page : "source_base_id"
    
    source_run_report ||--o{ source_run_page : "source_run_report_base_id"
```

## Schema Organization

### agency_management
**Purpose**: Manages government agencies and organizational entities
- Contains agency information, metadata, and archival status
- Supports both client agencies and API sources
- Tracks data integrity with hashing and archival status

### data_collection  
**Purpose**: Manages data sources and collected files
- **source**: Configuration for websites, APIs, and upload sources
- **source_file**: Individual files with processing pipeline status
- Tracks ETL pipeline progress from raw to cleaned content

### monitoring
**Purpose**: Tracks processing execution and error logging
- **source_run_report**: High-level processing execution reports
- **source_run_page**: Detailed page-level processing logs
- Provides comprehensive audit trail for all operations

## Key Design Patterns

### UUID-based Identification
- **id**: Technical primary key (UUID)
- **base_id**: Business identifier for external references
- Supports versioning and historical tracking

### Soft Deletion
- **is_deleted**: Logical deletion flag on all tables
- Preserves data for audit and recovery purposes
- Queries filter by `is_deleted = FALSE`

### Timestamp Tracking
- **created_at**: Record creation time
- **updated_at**: Last modification time
- **Processing timestamps**: Track ETL pipeline stages

### Status Enums
- **agency_type**: 'client' | 'api'
- **source_type**: 'url_to_scrape' | 'file' | 'api'
- **source_status_type**: 'new' | 'running' | 'finished' | 'failed'
- **source_file_status_type**: 'scraping' | 'cleaning' | 'finished' | 'not_found' | 'failed'
- **source_file_type**: 'scraped_file' | 'uploaded_file' | 'api_file'

## Relationships

### Primary Relationships
1. **Agency → Source** (1:N): Each agency can have multiple data sources
2. **Source → Source File** (1:N): Each source contains multiple files
3. **Source Run Report → Source Run Page** (1:N): Reports contain page-level logs

### Cross-Schema References
All tables reference `agency_base_id` for data partitioning and access control:
- Enables agency-specific data isolation
- Supports multi-tenant data access patterns
- Facilitates data export and archival by agency

## Data Pipeline Tracking

### File Processing States
```mermaid
stateDiagram-v2
    [*] --> scraping: File discovered
    scraping --> cleaning: Content extracted
    cleaning --> finished: Text processed
    scraping --> not_found: URL inaccessible
    scraping --> failed: Processing error
    cleaning --> failed: Cleaning error
    finished --> [*]: Ready for use
    not_found --> [*]: Marked as unavailable
    failed --> [*]: Error logged
```

### Source Processing States
```mermaid
stateDiagram-v2
    [*] --> new: Source created
    new --> running: Processing started
    running --> finished: All files processed
    running --> failed: Processing error
    finished --> running: Scheduled update
    failed --> running: Retry processing
```

## Indexes and Performance

### Primary Indexes
- **Composite indexes** on frequently queried column combinations
- **Timestamp indexes** for time-based queries and sorting
- **Status indexes** for filtering by processing state
- **Foreign key indexes** for join performance

### Query Patterns
- **Agency-based filtering**: Most queries filter by `agency_base_id`
- **Status-based queries**: Filter by processing status
- **Time-based queries**: Sort and filter by timestamps
- **Soft delete filtering**: All queries exclude deleted records

## Data Types and Constraints

### UUID Fields
- **id**: Technical primary key
- **base_id**: Business identifier with uniqueness constraints
- **Foreign keys**: Reference base_id fields for logical relationships

### Text Fields
- **Names and titles**: Variable length text
- **URLs**: Full URL storage for source tracking
- **File paths**: Blob storage references
- **Error messages**: Detailed error information

### Timestamp Fields
- **WITH TIME ZONE**: All timestamps include timezone information
- **Default values**: CURRENT_TIMESTAMP for audit fields
- **Nullable timestamps**: Optional for processing-specific times

### Boolean Flags
- **Default FALSE**: Most boolean flags default to false
- **Processing flags**: Track current operation state
- **Configuration flags**: Control automated behavior

## Extensions and Features

### PostgreSQL Extensions
- **uuid-ossp**: UUID generation functions
- **hstore**: Key-value storage support (future use)

### Schema Permissions
- **PUBLIC usage**: All schemas accessible to application users
- **Privilege grants**: Full table access within schemas
- **Public schema restriction**: Prevents accidental public table creation

## Data Integrity

### Referential Integrity
- Foreign key relationships via `base_id` references
- Cascade rules for data consistency
- Constraint validation for enum types

### Data Quality
- **Hash fields**: Content fingerprinting for duplicate detection
- **Timestamp consistency**: Processing pipeline time tracking
- **Status validation**: Enum constraints for valid states

### Audit Trail
- **Comprehensive logging**: All operations logged in monitoring schema
- **Error tracking**: Detailed error capture and classification
- **Processing history**: Complete pipeline execution tracking

## Future Considerations

### Scalability
- **Partitioning**: Consider table partitioning by agency or time
- **Read replicas**: Separate read/write database instances
- **Index optimization**: Monitor and optimize based on query patterns

### Analytics
- **Materialized views**: Pre-computed analytics tables
- **Time-series data**: Enhanced monitoring and metrics
- **Data warehouse**: Separate analytics database for reporting