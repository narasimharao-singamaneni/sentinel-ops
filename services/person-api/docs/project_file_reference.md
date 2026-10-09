# Project File Reference

## `person_api_fast/`

### `app/`
- `__init__.py` — Python package init
- `main.py` — FastAPI app instance, middleware, router mounting, health check
- `config.py` — `Settings` from `.env` (DATABASE_URL, MAX_CONCURRENT, CACHE_TTL, etc.)
- `database.py` — async engine, session factory, `get_session` dependency
- `models.py` — SQLAlchemy `Worker` ORM model
- `schemas.py` — Pydantic models: `WorkerCreate`, `WorkerUpdate`, `WorkerOut`, `WorkerListResponse`
- `workers.py` — `create_worker_router()` factory, cached GET, invalidation on write
- `cache.py` — aiocache helpers: `get_worker`, `set_worker`, `invalidate_worker`
- `middleware.py` — `ConcurrencyLimiterMiddleware` (asyncio.Semaphore)

### `tests/`
- `conftest.py` — SQLite in-memory engine, fixtures, DB setup/teardown
- `test_active.py` — 17 active worker CRUD + cache tests
- `test_inactive.py` — 14 inactive worker CRUD tests
- `test_factory.py` — 6 router factory isolation + route count tests
- `test_concurrency.py` — 7 concurrent request limiting tests

### `docs/`
- `api_specification.md` — full endpoint reference
- `project_file_reference.md` — this file

### Root
- `main.py` — uvicorn entry point (`python main.py`)
- `pyproject.toml` — project metadata, dependencies
- `.env.example` — environment template
- `README.md` — setup and usage
