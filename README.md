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

### Feature Highlights

- **Bulk Source File Operations**: Bulk refresh, bulk include/exclude, and bulk delete are supported for source files.
- **First-Time Scraping on Source Creation**: Creating a standard web source (adding an URL) triggers initial scraping, then pauses before cleaning and moves the source to `in_review`; cleaning starts only when the user clicks **Start Cleaning**.
- **Narrowed Web Scraping**: For a specified URL, scraping includes that URL and only its child/subpages; parent paths and sibling/parallel paths are excluded.
- **Pre-Selected URL List Addition**: Users can create a source from a pre-selected URL list and trigger scraping/cleaning for those URLs only.
- **LLM Extraction Quality Control Modes**: Source-level quality control supports `basic`, `comprehensive`, or none, and controls cleaning-time LLM flags.
- **Single Agency Enforcement**: CKB allows only one agency per deployment; creating additional agencies is blocked with `409 Conflict`.

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
| **Cleaning**        | Content cleaning and text extraction      | Python/FastAPI      | 8123 |
| **File Processing** | File upload and storage management        | Python/FastAPI      | 8888 |
| **Scheduler**       | Task scheduling and automation            | Python/FastAPI      | 8003 |
| **Data Export**     | Database export and archival              | Python/FastAPI      | 8002 |

### Supporting Components

- **PostgreSQL**: Primary database for structured data
- **S3 Storage**: Blob storage for files and content
- **Liquibase**: Database schema migrations
- **Celery**: Background task processing
- **HashiCorp Vault**: Secrets management and credential rotation for service integrations (e.g. Azure OpenAI credentials used by the Cleaning Service)

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

The cleaning service has a dedicated automated test suite (unit, API contract, and integration tests) that runs on every pull request via GitHub Actions. See [Cleaning Service — Testing](./cleaning/README.md#testing) for how to run tests locally.

### Python Development Standards

The five Python services in this repo — `cleaning`, `data-export`, `file-processing`, `scheduler`, `scrapper` — share one toolchain, one virtualenv, and one set of formatting/typing/test rules.

#### What changed vs. the legacy layout

- **No more per-service `requirements.txt`.** Each service's runtime dependencies are declared in the top-level `pyproject.toml` under `[project.optional-dependencies]` (one group per service). The shared dev toolchain (ruff, pyright, pytest, pre-commit, …) lives under `[dependency-groups].dev`.
- **One lockfile (`uv.lock`) at the repo root** covers every service and the dev tools. CI fails if the lockfile drifts from `pyproject.toml`.
- **All versions are pinned exactly with `==`.** No `>=`, no `~=`. Bumping a dep is an explicit, reviewable change.
- **Single Python version (`3.12.10`)** declared in `.python-version` and `requires-python = "==3.12.10"`. Both [uv](https://docs.astral.sh/uv/) and pyright read it.
- **Docker images consume the same `pyproject.toml` + `uv.lock`** via `uv sync --frozen --no-dev --extra <service>` into `/opt/venv`. Local dev and CI install from exactly the same lock as the runtime image.

#### Repository layout

```
pyproject.toml         # all deps + tool config (ruff, pyright)
uv.lock                # locked versions for every extra
.python-version        # 3.12.10
pytest.ini             # test config (pythonpath = cleaning)
.pre-commit-config.yaml
.gitleaks.toml         # gitleaks rules + allowlists
cleaning/              # one service per top-level dir, no requirements.txt
data-export/
file-processing/
scheduler/
scrapper/
tests/                 # pytest tests (currently cleaning-service tests)
```

#### Local setup

```bash
# 1. Install uv (https://docs.astral.sh/uv/getting-started/installation/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install the pinned Python interpreter (uses .python-version)
uv python install

# 3. Install deps. Three useful shapes:

# a) One service + dev tools (fastest; matches per-service Dockerfile + dev tools)
uv sync --frozen --extra cleaning --group dev

# b) All services + dev tools (what CI does; also what you need to run pyright
#    cleanly across the whole repo, since pyright checks all five services)
uv sync --frozen --all-extras --group dev

# c) Production-style for a service (no dev tools — what the Dockerfile runs)
uv sync --frozen --no-dev --extra cleaning
```

The venv lives at `.venv/` at the repo root. Activate manually with `source .venv/bin/activate`, or just prefix every tool call with `uv run`.

#### Adding or upgrading a dependency

1. Edit `pyproject.toml` — add the package with an exact `==` pin to the right `[project.optional-dependencies]` group (or to `[dependency-groups].dev` for tooling).
2. Run `uv lock` to refresh `uv.lock`.
3. Commit both files together. CI's `uv lock --check` will reject a `pyproject.toml` change without a matching lockfile update.

#### Formatting and linting — Ruff

Pinned to `ruff==0.13.3`. Config in `[tool.ruff]` of `pyproject.toml`:

- Line length **88**, 4-space indent, double quotes, `target-version = "py312"`.
- `fix = false` at the project level — the formatter does not auto-rewrite when CI runs; you opt in locally with `--fix`.
- Lint rule sets enabled: `E4, E7, E9, F, B, T20, N, ANN, ERA, PERF` (pycodestyle errors, pyflakes, bugbear, no-print, naming, missing annotations, no commented-out code, perf hints).
- Sibling Bürokratt language services (`Authentication-Layer`, `CronManager`, `DataMapper`, `Resql`, `Ruuter`, `TIM`) are excluded — they ship from their own repos and just happen to share this working tree.

```bash
uv run ruff format .             # rewrite files
uv run ruff format --check .     # fail if anything would change (what CI runs)
uv run ruff check .              # lint
uv run ruff check --fix .        # lint + auto-fix safe issues
```

#### Type checking — Pyright

Pinned to `pyright==1.1.405`. Config in `[tool.pyright]` of `pyproject.toml`:

- `typeCheckingMode = "standard"` (not strict — strict would require a much bigger annotation pass).
- `pythonVersion = "3.12.10"`, reads `venvPath = "."` + `venv = ".venv"`.
- `include` lists the five service dirs; `tests/` is excluded from the type-check pass (tests rely on dynamic `unittest.mock` patches that fight strict typing).
- **Per-service `executionEnvironments`** scope each service's import resolution to its own directory. Both `scheduler/api/` and `scrapper/api/` exist as siblings, and a single global `extraPaths` would make `from api.models import …` always pick the alphabetically-first match. Each `[[tool.pyright.executionEnvironments]]` block mirrors what `Dockerfile WORKDIR=/app + COPY <service>/ /app/` does at runtime.

```bash
uv run pyright          # check every included service
uv run pyright cleaning # check just one service
```

> Pyright runs Node under the hood. If your system Node is older than v18 (older Ubuntu/snap installs ship Node 6), `uv run pyright` errors out with a JS syntax error before pyright even starts. Either upgrade Node or rely on CI for the type check.

#### Tests — Pytest

Pinned to `pytest==8.3.5` (with `pytest-cov`, `pytest-timeout`). Config in `pytest.ini`:

- `testpaths = tests` — all test files live under `tests/` at the repo root.
- `pythonpath = cleaning` — so `from worker.tasks import …` in `tests/test_tasks.py` resolves (`worker/` lives at `cleaning/worker/`). This replaces the older `PYTHONPATH=cleaning` env-var workaround.
- Currently the test suite is cleaning-service-only: `tests/test_tasks.py` (unit, mocked), `tests/test_api.py` and `tests/test_integration.py` (require the cleaning Docker stack — the `cleaning_stack` fixture in `tests/conftest.py` brings it up).

```bash
uv run pytest                          # everything (integration tests need Docker)
uv run pytest tests/test_tasks.py -v   # unit tests only, no containers needed
```

The cleaning service also has its own dedicated CI workflow ([test-cleaning.yml](./.github/workflows/test-cleaning.yml)) that runs both unit and integration jobs on PRs touching `cleaning/`, `tests/`, or the lock.

#### Secret scanning — Gitleaks

`gitleaks v8.21.2` runs in CI and as a pre-commit hook. `.gitleaks.toml` extends the default ruleset and carries project-specific allowlists for known false positives (e.g. SHA-style hex hashes in DB seed fixtures that the generic-api-key rule otherwise flags).

```bash
docker run --rm -v "$PWD":/code zricethezav/gitleaks:latest \
  detect --source=/code --redact --no-banner   # exact CI invocation
```

#### Pre-commit hooks

Pinned to `pre-commit==4.3.0`. Hooks defined in `.pre-commit-config.yaml`:

- `ruff-pre-commit` (`v0.13.3`) — format + lint
- `uv-pre-commit` (`0.11.8`) — `uv lock --check` so a dep change can't land without the lockfile update
- `gitleaks` (`v8.21.2`)

```bash
uv run pre-commit install              # one-time, installs the git hook
uv run pre-commit run --all-files      # run every hook against the whole tree
```

#### CI quality gates

`.github/workflows/python-checks.yml` runs on every push and PR to `wip`/`dev`/`main`:

1. `uv lock --check` — lockfile is in sync with `pyproject.toml`
2. `uv sync --frozen --all-extras --group dev`
3. `uv run ruff format --check .`
4. `uv run ruff check .`
5. `uv run pyright`
6. `uv run pytest`

A separate `gitleaks` job runs `gitleaks detect` against the full history.

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
