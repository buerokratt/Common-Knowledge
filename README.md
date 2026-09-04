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

This is the complete, ordered walkthrough to bring the whole system up locally from a fresh
clone. Do the steps in order — several will fail if run out of sequence (e.g. the migration
scripts need the Docker network that `docker-compose up` creates).

### Prerequisites

- Docker, with **Docker Compose v2** (`docker compose ...`) 
- Git, and network access to `github.com/buerokratt` to clone the sibling platform repos.
- (Optional) S3-compatible storage (AWS S3, MinIO, …) for real file uploads. Local dev works
  with placeholder S3 config (step 4) for everything except actual upload/download/zip.

> **Note:** PostgreSQL, OpenSearch, RabbitMQ and HashiCorp Vault all run as containers from
> `docker-compose.yml` — you do **not** need to install them on the host.

### Step 1 — Clone this repository

```bash
git clone https://github.com/buerokratt/Common-Knowledge.git
cd Common-Knowledge
```

### Step 2 — Build the dependency service images

`docker-compose.yml` builds the CKB-owned services (GUI, scrapper, cleaning, file-processing,
scheduler, data-export, search-service) from this repo, but it expects several **Bürokratt
platform images to already exist locally** — it references them by bare tag (`image: ruuter`,
`image: resql`, …) with no build context, so `docker-compose up` fails with a "pull
access denied / no such image" error if they are missing.

These images are **not published to a registry**; you build them yourself from sibling
Bürokratt repos. Clone each one **outside** this repo (e.g. one directory up) and build the
listed tag. Use the `dev` branch for all of them:

```bash
cd ..   # build the sibling repos next to Common-Knowledge, not inside it

# 1. Ruuter — request orchestration engine (external + internal APIs)
git clone -b dev https://github.com/buerokratt/Ruuter.git
docker build -t ruuter ./Ruuter

# 2. Resql — named-SQL query engine
git clone -b dev https://github.com/buerokratt/Resql.git
docker build -t resql ./Resql

# 3. DataMapper — Handlebars/JSON response transforms
git clone -b dev https://github.com/buerokratt/DataMapper.git
docker build -t data-mapper ./DataMapper

# 4. TIM — authentication/token service
git clone -b dev https://github.com/buerokratt/TIM.git
docker build -t tim ./TIM

# 5. Authentication Layer — builds from Dockerfile.dev (note the -f flag)
git clone -b dev https://github.com/buerokratt/Authentication-Layer.git
docker build -f ./Authentication-Layer/Dockerfile.dev -t authentication-layer ./Authentication-Layer

# 6. CronManager — scheduled-job runner. Build the Python-enabled image (Dockerfile.python)
#    so jobs that shell out to Python work; the plain Dockerfile is Java-only.
git clone -b dev https://github.com/buerokratt/CronManager.git
docker build -f ./CronManager/Dockerfile.python -t cron-manager ./CronManager

cd Common-Knowledge   # back to this repo for the remaining steps
```

Verify all six tags exist before continuing:

```bash
docker images | grep -E "ruuter|resql|data-mapper|tim|authentication-layer|cron-manager"
```


### Step 3 — Create the root `.env`

`docker-compose` auto-loads a **root `.env` file** (git-ignored, so it is not in a fresh
clone — you must create it). It is **required**: the file-processing service instantiates its
S3 client at import time and **crashes on startup with `ValueError: Invalid endpoint:` if
`S3_ENDPOINT_URL` is empty or unset**. Create `.env` in the repo root:

```env
# S3 / blob storage — REQUIRED (file-processing crashes without a valid, non-empty endpoint).
# For local development without a real bucket, placeholders let the stack boot; actual
# upload/download/zip operations will fail until you point these at a real S3 or local MinIO.
AWS_ACCESS_KEY_ID=local-dev-placeholder
AWS_SECRET_ACCESS_KEY=local-dev-placeholder
AWS_REGION=us-east-1
S3_BUCKET_NAME=ckb-local
S3_ENDPOINT_URL=http://localhost:9000

# Optional file-processing tunables (compose supplies these defaults if omitted):
# AUTO_CLEANUP_COMPLETED_TASKS=true
# COMPLETED_TASK_CLEANUP_DELAY_MINUTES=5
# PERIODIC_CLEANUP_INTERVAL_MINUTES=60
# MAX_TASK_AGE_HOURS=24
```

Database connection and inter-service URLs are already wired in `docker-compose.yml` and
`constants.ini` — you do not set `DATABASE_URL` or the Ruuter URLs by hand for the local stack.

### Step 4 — Start the stack

```bash
docker-compose up -d          # builds CKB-owned images, starts everything (incl. the step-2 images)
docker-compose ps             # watch until services are Up / healthy
```

This also creates the `bykstack` Docker network that the migration scripts in step 5 rely on.

### Step 5 — Run database migrations

Schema is managed by **Liquibase** (not ORM migrations), applied through a helper script that
runs a Liquibase container on the `bykstack` network against the `database` container:

```bash
./migrate.sh
```

### Step 6 — Load test data (optional, but needed for the sample login below)

```bash
./load-test-data.sh
```

### Step 7 — Verify it works

```bash
# GUI (dev container) — published on port 3001
open http://localhost:3001

# External API (Ruuter external) is on port 8086. Endpoints are behind an auth guard, so an
# unauthenticated call returns {"response":"unauthorized"} (HTTP 403) — that alone confirms
# the external API + auth chain are up.
curl http://localhost:8086/ckb/agency/all

# Full end-to-end check: log in. With test data loaded (step 6) this returns HTTP 200 and a
# JWT, exercising Ruuter → Authentication-Layer → TIM → Resql → PostgreSQL.
curl -X POST -H "Content-Type: application/json" \
  -d '{"login":"EE30303039914","password":"OK"}' \
  http://localhost:8086/ckb/auth/login
```

> **Ports:** the GUI dev container is published on **3001** and the external Ruuter API on
> **8086** (internal Ruuter on 8089). Earlier revisions of these docs mentioned 3000/8080 —
> the actual published host ports in `docker-compose.yml` are 3001 and 8086.

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

Ports below are the **published host ports** in `docker-compose.yml`.

| Service             | Purpose                                   | Technology          | Port |
| ------------------- | ----------------------------------------- | ------------------- | ---- |
| **GUI**             | Web interface for CKB management          | React/TypeScript    | 3001 |
| **Ruuter External** | Public API with authentication            | Ruuter YAML configs | 8086 |
| **Ruuter Internal** | Internal service communication            | Ruuter YAML configs | 8089 |
| **Resql**           | SQL query engine and database abstraction | SQL with metadata   | -    |
| **Scrapper**        | Web scraping and content extraction       | Python/Scrapy       | 8080 |
| **Cleaning**        | Content cleaning and text extraction      | Python/FastAPI      | 8123 |
| **File Processing** | File upload and storage management        | Python/FastAPI      | 8888 |
| **Scheduler**       | Task scheduling and automation            | Python/FastAPI      | 8124 |
| **Data Export**     | Database export and archival              | Python/FastAPI      | 8889 |

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

The full, ordered setup walkthrough — clone, build the dependency images, create `.env`,
start the stack, migrate, load test data, verify — lives in
[**Quick Start**](#quick-start) above. Once the stack is up, useful day-to-day commands:

```bash
docker-compose up -d                        # start everything
docker-compose up -d gui scrapper-server    # start a subset
docker-compose ps                           # health
docker-compose logs -f <service>            # follow logs
docker-compose down                         # stop the stack (keeps volumes/data)
./migrate.sh                                # apply new Liquibase migrations
```

### Testing

```bash
# Run API tests
curl http://localhost:8086/ckb/agency/all

# Test scraping functionality (scrapper service is published on port 8080)
curl -X POST http://localhost:8080/specified-pages-scrapper-task \
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
curl -X POST -H "Content-Type: application/json" -d '{
  "login": "EE30303039914",
  "password": "OK"
}' http://localhost:8086/ckb/auth/login 

# Use token in subsequent requests
curl -H "Authorization: Bearer <token>" \
  http://localhost:8086/ckb/agency/all
```

### Common Operations

```bash
# List all agencies
curl http://localhost:8086/ckb/agency/all

# Create new source
curl -X POST http://localhost:8086/ckb/source/add \
  -H "Content-Type: application/json" \
  -d '{"agency_id": "agency1", "name": "Source Name", "url": "https://example.com"}'

# Trigger scraping
curl -X POST http://localhost:8086/ckb/source/refresh \
  -d '{"source_id": "source1"}'

# Check processing status
curl http://localhost:8086/ckb/reports/all
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
