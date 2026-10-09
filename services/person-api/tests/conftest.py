import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import patch

# MUST set test mode BEFORE any app imports
os.environ["MAX_CONCURRENT"] = "10"
os.environ["OTEL_TEST_MODE"] = "1"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_db.sqlite"

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

_engine = create_async_engine("sqlite+aiosqlite:///./test_db.sqlite")

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


@pytest.fixture
def mock_semaphore():
    mock_sema = asyncio.Semaphore(100)
    with patch("app.middleware._semaphore", mock_sema):
        yield mock_sema


@pytest.fixture
def concurrency_semaphore():
    real_sema = asyncio.Semaphore(settings.MAX_CONCURRENT)
    original_timeout = settings.SEMAPHORE_TIMEOUT
    settings.SEMAPHORE_TIMEOUT = 0.05  # 50ms — short enough to trigger rejections
    with patch("app.middleware._semaphore", real_sema):
        yield real_sema
    settings.SEMAPHORE_TIMEOUT = original_timeout


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


@pytest.fixture
async def inactive_worker(db_session: AsyncSession) -> Worker:
    return await create_test_worker(db_session, name="inactive_user", status="inactive")


@pytest.fixture
async def seeded_active_workers(db_session: AsyncSession) -> list[Worker]:
    workers = []
    for i in range(25):
        w = Worker(id=str(uuid.uuid4()), name=f"worker_{i}", status="active")
        workers.append(w)
        db_session.add(w)
    await db_session.flush()
    await db_session.commit()
    for w in workers:
        await db_session.refresh(w)
    return workers


@pytest.fixture
async def seeded_inactive_workers(db_session: AsyncSession) -> list[Worker]:
    workers = []
    for i in range(25):
        w = Worker(id=str(uuid.uuid4()), name=f"inactive_worker_{i}", status="inactive")
        workers.append(w)
        db_session.add(w)
    await db_session.flush()
    await db_session.commit()
    for w in workers:
        await db_session.refresh(w)
    return workers