# Scheduler Service

A FastAPI service that manages cron-based scheduling for the Common Knowledge Base pipeline. The service calculates next execution times for scheduled tasks and integrates with automated data processing workflows.

## Overview

The scheduler service is responsible for:
- Calculating next execution times for cron expressions
- Supporting timezone-aware scheduling (default: Europe/Tallinn)
- Providing scheduling utilities for pipeline automation
- Managing periodic tasks for data processing workflows

## Architecture

```
scheduler/
├── api/                  # FastAPI application
│   ├── app.py           # Main API endpoints
│   ├── models.py        # Pydantic models for scheduling
│   └── __init__.py
├── debug.py             # Debug utilities
├── requirements.txt     # Python dependencies
└── Dockerfile          # Container configuration
```

## Features

### Scheduling Capabilities

1. **Cron Expression Parsing**: Supports standard cron syntax for flexible scheduling
2. **Timezone Support**: Configurable timezone handling with Europe/Tallinn as default
3. **Next Run Calculation**: Precise calculation of next execution timestamps
4. **Pipeline Integration**: Seamless integration with automated data processing

### Automated Tasks

The scheduler manages these recurring tasks (configured in `DSL/CronManager/scheduler.yml`):

1. **Scheduler Check** (every 5 minutes): 
   - URL: `http://ruuter-internal:8089/ckb/pipeline/scheduler-check-for-unscheduled-records`
   - Purpose: Identifies sources that need scheduling

2. **Pipeline Trigger** (every 5 minutes):
   - URL: `http://ruuter-internal:8089/ckb/pipeline/trigger-pipeline-for-sceduled-sources`
   - Purpose: Executes scheduled scraping tasks

3. **Data Zipping** (hourly):
   - URL: `http://ruuter-internal:8089/ckb/pipeline/zip`
   - Purpose: Archives and compresses processed data

## API Endpoints

### POST /render_next_run
Calculate the next execution time for a given cron expression.

**Request Body:**
```json
{
  "cron_expression": "0 */5 * * * ?",
  "timezone": "Europe/Tallinn"
}
```

**Response:**
```json
{
  "timestamp": "2025-01-01T12:00:00+02:00"
}
```

## Data Models

### SchedulerEntity
```python
{
  "cron_expression": "str",    # Standard cron syntax
  "timezone": "str"            # Timezone identifier (default: Europe/Tallinn)
}
```

### NextTimeToRun
```python
{
  "timestamp": "datetime"      # Next execution timestamp with timezone
}
```

## Environment Variables

- `TIMEZONE`: Default timezone for scheduling (default: Europe/Tallinn)
- `CRON_CONFIG_PATH`: Path to cron configuration file

## Dependencies

- **FastAPI 0.115.12**: Web framework for API endpoints
- **croniter 6.0.0**: Cron expression parsing and calculation
- **pytz**: Timezone handling and conversion

## Cron Expression Format

The service supports standard cron expressions with second precision:

```
┌───────────── second (0-59)
│ ┌───────────── minute (0-59)
│ │ ┌───────────── hour (0-23)
│ │ │ ┌───────────── day of month (1-31)
│ │ │ │ ┌───────────── month (1-12)
│ │ │ │ │ ┌───────────── day of week (0-6)
│ │ │ │ │ │
│ │ │ │ │ │
* * * * * *
```

Examples:
- `0 */5 * * * ?`: Every 5 minutes
- `0 0 */1 * * ?`: Every hour
- `0 0 0 * * ?`: Daily at midnight

## Running the Service

### Development

```bash
# Install dependencies
pip install -r requirements.txt

# Start the FastAPI server
uvicorn api.app:app --host 0.0.0.0 --port 8003 --reload
```

### Docker

```bash
# Build the image
docker build -t scheduler .

# Run the container
docker run -p 8003:8003 scheduler
```

## Integration

The scheduler service integrates with:
- **Ruuter Internal**: Receives scheduling requests for pipeline tasks
- **CronManager**: Configuration-driven task scheduling
- **Pipeline Services**: Triggers automated scraping and processing workflows
- **Database**: Coordinates with data processing timing requirements

## Configuration

Scheduled tasks are defined in `DSL/CronManager/scheduler.yml` with:
- **Trigger**: Cron expression for execution timing
- **Type**: HTTP request type
- **Method**: HTTP method (GET/POST)
- **URL**: Target endpoint for task execution

## Use Cases

1. **Periodic Scraping**: Schedule regular content updates from sources
2. **Data Processing**: Coordinate ETL pipeline execution timing
3. **Maintenance Tasks**: Schedule cleanup and archival operations
4. **Resource Management**: Time-based resource allocation and cleanup