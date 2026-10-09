import asyncio

from fastapi import FastAPI
from starlette.responses import RedirectResponse, JSONResponse
from starlette.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.logging import setup_logger
from app.database import engine, async_session_factory
from app.error_reporter import SentinelErrorReporterMiddleware
from app.http_middleware import RequestIdMiddleware, StructuredRequestLoggingMiddleware
from app.middleware import ConcurrencyLimiterMiddleware
from app.otel import configure_tracer
from app.schemas import ErrorDetail
from app.telemetry import router as telemetry_router
from app.workers import create_worker_router

logger = setup_logger()

app = FastAPI(
    title="Person API",
    version="1.0.1",
    description=(
        "API for managing corporate workers. "
        "Scalable to 50K active and 150K inactive employees. "
        "Supports up to 10 concurrent requests. "
        "Backed by PostgreSQL."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(ConcurrencyLimiterMiddleware)

# --- OpenTelemetry Tracing ---
_tracer = configure_tracer()

# Instrument FastAPI automatically (creates spans for every request)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: E402

FastAPIInstrumentor.instrument_app(app)

# Mark 4xx/5xx responses as ERROR on the span (instrumentor only marks exceptions)
from app.otel_middleware import OtelStatusMiddleware  # noqa: E402

app.add_middleware(OtelStatusMiddleware)

# Error reporting sits inside request-id (reads the request id from scope
# state) and outside the concurrency limiter (sees rejected 503 responses).
app.add_middleware(SentinelErrorReporterMiddleware)

# Request identity (X-Request-ID) then structured request logging, outermost.
app.add_middleware(RequestIdMiddleware)
app.add_middleware(StructuredRequestLoggingMiddleware)

app.include_router(
    create_worker_router("active", cache_enabled=True, default_limit=settings.DEFAULT_PAGE_SIZE),
    prefix="/api/v1/active",
    tags=["active_workers"],
)

app.include_router(
    create_worker_router("inactive", cache_enabled=False, default_limit=settings.DEFAULT_PAGE_SIZE),
    prefix="/api/v1/inactive",
    tags=["inactive_workers"],
)

app.include_router(telemetry_router)


@app.get("/", include_in_schema=False)
async def redirect_to_docs():
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/test/delay", include_in_schema=False)
async def test_delay(delay: float = 0.2):
    await asyncio.sleep(delay)
    return {"delayed": True}


@app.get("/test/db-delay", include_in_schema=False)
async def test_db_delay(delay: float = 0.2):
    """Execute a real PostgreSQL pg_sleep(N) through the normal session.

    PostgreSQL only. delay <= DB_QUERY_TIMEOUT -> 200, otherwise 504.
    On 504 the DB session/connection and any request state are released.
    """
    if engine.dialect.name != "postgresql":
        return JSONResponse(
            status_code=503,
            content={"detail": "engine unsupported: /test/db-delay requires PostgreSQL"},
        )

    from sqlalchemy import text

    session = async_session_factory()
    try:
        try:
            await asyncio.wait_for(
                session.execute(text(f"SELECT pg_sleep({float(delay)})")),
                timeout=settings.DB_QUERY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await session.close()
            return JSONResponse(
                status_code=504,
                content={
                    "detail": (
                        f"database query timeout after {settings.DB_QUERY_TIMEOUT}s "
                        f"(pg_sleep {float(delay)}s exceeded DB_QUERY_TIMEOUT)"
                    )
                },
            )
        return {"delayed": True, "db_delay": True, "delay": float(delay)}
    finally:
        await session.close()
