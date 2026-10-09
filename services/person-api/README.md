# Person API

FastAPI-based CRUD API for managing corporate workers. Scales to 50K active and 150K inactive employees with support for 10 concurrent requests. Backed by PostgreSQL.

## Tech Stack

- **Framework:** FastAPI (async)
- **Database:** PostgreSQL (via asyncpg + SQLAlchemy 2.0)
- **Caching:** aiocache (SimpleMemoryCache) for active workers
- **Concurrency:** asyncio semaphore (10 caller limit)
- **Package Manager:** uv

## Prerequisites

- Python >= 3.12
- PostgreSQL >= 14 (listening on `localhost:5432`, no auth required)
- uv (install: `curl -LsSf https://astral.sh/uv/install.sh | sh`)

## Setup

```bash
# Create production database (if not exists)
psql -h localhost -d template1 -c "CREATE DATABASE person_api;"

# Create the workers table
psql -h localhost -d person_api < - <<'SQL'
CREATE TABLE IF NOT EXISTS workers (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_workers_status ON workers (status);
CREATE INDEX IF NOT EXISTS idx_workers_name ON workers (name);
SQL

# Install dependencies (from the repo root — one shared env for all suites)
uv sync

# Run the server
python main.py

# Open browser: http://localhost:8080 (redirects to /docs)
```

## Database Schema

A single `workers` table with a `status` column (`'active'` or `'inactive'`) and indexes on `status` and `name`.

## API Endpoints

### Active Workers (`/api/v1/active`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/workers` | Create active worker |
| GET | `/workers/{id}` | Get active worker (cached) |
| PUT | `/workers/{id}` | Update active worker (cache invalidated) |
| DELETE | `/workers/{id}` | Delete active worker (cache invalidated) |
| GET | `/workers?limit=20&offset=0` | List active workers (paginated) |

### Inactive Workers (`/api/v1/inactive`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/workers` | Create inactive worker |
| GET | `/workers/{id}` | Get inactive worker |
| PUT | `/workers/{id}` | Update inactive worker |
| DELETE | `/workers/{id}` | Delete inactive worker |
| GET | `/workers?limit=20&offset=0` | List inactive workers (paginated) |

### System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Redirects to Swagger docs |
| GET | `/health` | Health check |

## Concurrency

Maximum 10 concurrent requests. Exceeding callers receive HTTP 503.

## Caching

Active worker `GET /workers/{id}` is cached for 600 seconds. Cache is invalidated on `PUT` and `DELETE`. Inactive worker endpoints are not cached.

## Testing

```bash
# Run all 41 tests (uses SQLite, no external DB needed)
uv run pytest tests/ -v

# Concurrency tests (uses mock semaphore)
uv run pytest tests/test_concurrency.py -v

# Run coverage
uv run pytest tests/ -v --cov=app --cov-report=term-missing
```

Tests cover CRUD operations (active & inactive), cache behavior, router factory isolation, and 10-concurrent-caller limiting.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| DATABASE_URL | `postgresql+asyncpg://localhost:5432/person_api` | Production DB |
| TEST_DATABASE_URL | `postgresql+asyncpg://localhost:5432/test_person_api` | Test DB |
| MAX_CONCURRENT | 10 | Concurrent request limit |
| CACHE_TTL | 600 | Cache TTL in seconds |
| DEFAULT_PAGE_SIZE | 20 | Default list page size |
| MAX_PAGE_SIZE | 100 | Maximum list limit |
