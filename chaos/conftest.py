"""Chaos harness test configuration — reuses person-api test fixtures."""

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import patch

# MUST set test mode BEFORE any app imports
os.environ["MAX_CONCURRENT"] = "10"
os.environ["OTEL_TEST_MODE"] = "1"

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_session
from app.main import app
from app.otel import configure_tracer, get_test_exporter
from app.models import Base, Worker

# Ensure tracer is configured with in-memory exporter for tests
configure_tracer()

_engine = create_async_engine("sqlite+aiosqlite:///./test_chaos_db.sqlite")

async_session_factory = async_sessionmaker(
    _engine, class_=AsyncSession, expire_on_commit=False
)


async def _get_test_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

app.dependency_overrides[get_session] = _get_test_session


async def _create_table():
    async with _engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS workers"))
        await conn.execute(text("""
            CREATE TABLE workers (
                id TEXT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_workers_status ON workers (status)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_workers_name ON workers (name)"))


async def _drop_table():
    async with _engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS workers"))


async def _clear_table():
    async with _engine.begin() as conn:
        await conn.execute(text("DELETE FROM workers"))


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    await _create_table()
    yield
    await _drop_table()


@pytest.fixture(autouse=True)
async def clear_db():
    await _clear_table()
    # Reset the concurrency limiter semaphore between tests for isolation.
    # Without this, the pool exhaustion scenario leaves the semaphore in a
    # used state, causing subsequent tests to see 503s unexpectedly.
    from app.middleware import reset_semaphore
    reset_semaphore()


@pytest.fixture(autouse=True)
def in_memory_exporter() -> InMemorySpanExporter:
    """Return the shared in-memory OTel exporter, cleared before each test."""
    exporter = get_test_exporter()
    exporter.clear()
    return exporter


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
        await session.commit()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def create_test_worker(
    session: AsyncSession,
    *,
    name: str = "test_worker",
    status: str = "active",
) -> Worker:
    worker = Worker(id=str(uuid.uuid4()), name=name, status=status)
    session.add(worker)
    await session.flush()
    await session.commit()
    await session.refresh(worker)
    return worker


@pytest.fixture
async def active_worker(db_session: AsyncSession) -> Worker:
    return await create_test_worker(db_session, name="active_user", status="active")
