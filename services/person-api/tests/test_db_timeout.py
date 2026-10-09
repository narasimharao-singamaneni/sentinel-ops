"""DB query timeout guard + /test/db-delay route.

SQLite (hermetic): route is engine-unsupported (503), no pg_sleep executed.
PostgreSQL (real): a real uvicorn server on a loopback port against real
PostgreSQL — 200 when delay <= DB_QUERY_TIMEOUT, 504 when it exceeds it,
and pool telemetry returns to baseline after the 504.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _pg_reachable() -> bool:
    code = (
        "import asyncio, asyncpg\n"
        "async def m():\n"
        "    c = await asyncio.wait_for(asyncpg.connect('postgresql://localhost:5432/person_api', timeout=3), 5)\n"
        "    await c.close()\n"
        "asyncio.run(m())\n"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=20,
        )
        return r.returncode == 0
    except Exception:
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestDbDelayUnderSQLite:
    async def test_db_delay_engine_unsupported_under_sqlite(
        self, client: httpx.AsyncClient
    ):
        resp = await client.get("/test/db-delay?delay=0.2")
        assert resp.status_code == 503
        detail = resp.json()["detail"].lower()
        assert "unsupported" in detail or "postgresql" in detail

    async def test_regular_delay_route_unaffected(self, client: httpx.AsyncClient):
        resp = await client.get("/test/delay?delay=0.05")
        assert resp.status_code == 200


class TestDbDelayAgainstRealPostgres:
    """Real pg_sleep against real PostgreSQL over real HTTP (no mocks)."""

    pytestmark = pytest.mark.skipif(
        not _pg_reachable(),
        reason="PostgreSQL unreachable at localhost:5432 (precondition not met)",
    )

    @pytest.fixture
    def pg_server(self):
        port = _free_port()
        env = os.environ.copy()
        env["DATABASE_URL"] = "postgresql+asyncpg://localhost:5432/person_api"
        env["DB_QUERY_TIMEOUT"] = "0.5"
        env.pop("OTEL_TEST_MODE", None)
        env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)

        cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        ready = False
        while time.time() < deadline:
            try:
                r = httpx.get(f"{base}/health", timeout=1.0)
                if r.status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        if not ready:
            proc.terminate()
            proc.wait(timeout=10)
            pytest.fail("person-api live server did not become healthy in 30s")

        yield base

        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

    def test_short_delay_succeeds(self, pg_server: str):
        resp = httpx.get(f"{pg_server}/test/db-delay?delay=0.2", timeout=10)
        assert resp.status_code == 200, resp.text

    def test_long_delay_times_out_with_504(self, pg_server: str):
        resp = httpx.get(f"{pg_server}/test/db-delay?delay=2", timeout=15)
        assert resp.status_code == 504, resp.text
        assert "timeout" in resp.json()["detail"].lower()

    def test_pool_returns_to_baseline_after_504(self, pg_server: str):
        httpx.get(f"{pg_server}/test/db-delay?delay=2", timeout=15)
        time.sleep(0.5)
        data = httpx.get(f"{pg_server}/api/v1/telemetry/pool", timeout=5).json()
        assert data["concurrency_in_use"] == 0
        assert data["checked_out"] == 0
