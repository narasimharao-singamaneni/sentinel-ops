"""No-mock live E2E harness (D12).

Spawns real subprocesses — person-api :8002, sentinel :8001, openwindow :8003 —
against real PostgreSQL. openwindow gets a unique temporary database
(openwindow_test_<uuid>) per session, migrated before use and dropped on
teardown. No mocks anywhere: real HTTP, real DB, real LLM at 127.0.0.1:8000/v1.
"""

import asyncio
import getpass
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = Path(__file__).resolve().parent / "logs"

PERSON_API_PORT = 8002
SENTINEL_PORT = 8001
OPENWINDOW_PORT = 8003

PERSON_API_URL = f"http://127.0.0.1:{PERSON_API_PORT}"
SENTINEL_URL = f"http://127.0.0.1:{SENTINEL_PORT}"
OPENWINDOW_URL = f"http://127.0.0.1:{OPENWINDOW_PORT}"

POSTGRES_ADDR = "localhost:5432"
LLM_URL = "http://127.0.0.1:8000/v1"
READY_TIMEOUT = 30.0


class PreflightMissing(Exception):
    """A live dependency is absent — the suite must skip with the address named."""


class HarnessError(Exception):
    """Harness setup/teardown failure that cannot be recovered from."""


def admin_user() -> str:
    return getpass.getuser()


def admin_dsn() -> str:
    return f"postgresql://{admin_user()}@{POSTGRES_ADDR}/postgres"


def target_dsn(db_name: str) -> str:
    return f"postgresql://{admin_user()}@{POSTGRES_ADDR}/{db_name}"


# ---------------------------------------------------------------------------
# Preflight (preconditions — skips with exact addresses, never a fake)
# ---------------------------------------------------------------------------

async def check_postgres(addr: str = POSTGRES_ADDR, timeout: float = 2.0) -> None:
    host, _, port = addr.partition(":")
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(host=host, port=int(port or 5432), database="postgres", timeout=timeout),
            timeout + 2.0,
        )
    except Exception as exc:  # noqa: BLE001 — any failure means "unreachable"
        raise PreflightMissing(
            f"PostgreSQL unreachable at {addr} ({type(exc).__name__}); "
            f"required for the live stack"
        ) from exc
    await conn.close()


async def check_llm(llm_url: str = LLM_URL, timeout: float = 3.0) -> None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.get(f"{llm_url}/models")
    except httpx.RequestError as exc:
        raise PreflightMissing(
            f"LLM unreachable at {llm_url} ({type(exc).__name__}); "
            f"live LLM tests require it"
        ) from exc


async def preflight(addr: str = POSTGRES_ADDR, llm_url: str = LLM_URL) -> None:
    await check_postgres(addr)
    await check_llm(llm_url)


# ---------------------------------------------------------------------------
# Temporary openwindow database lifecycle
# ---------------------------------------------------------------------------

async def create_temp_db() -> str:
    db_name = f"openwindow_test_{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(admin_dsn(), timeout=5.0)
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()
    print(f"[harness] created temp database {db_name}")
    return db_name


async def drop_temp_db(db_name: str) -> None:
    last_exc: Exception | None = None
    for _ in range(10):
        try:
            conn = await asyncpg.connect(admin_dsn(), timeout=5.0)
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            finally:
                await conn.close()
            print(f"[harness] dropped temp database {db_name}")
            return
        except (asyncpg.PostgresError, OSError) as exc:
            last_exc = exc
            await asyncio.sleep(0.5)
    raise HarnessError(f"could not drop temp database {db_name}: {last_exc}")


async def db_exists(db_name: str) -> bool:
    conn = await asyncpg.connect(admin_dsn(), timeout=5.0)
    try:
        return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name))
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Migrations (real manage.py run against the temp DB)
# ---------------------------------------------------------------------------

def run_migrations(db_dsn: str) -> None:
    env = os.environ.copy()
    env["DJANGO_DB_URL"] = db_dsn
    env["DJANGO_SETTINGS_MODULE"] = "openwindow.config.django_settings"
    result = subprocess.run(
        [sys.executable, "manage.py", "migrate", "--noinput"],
        cwd=str(PROJECT_ROOT / "apps" / "openwindow"),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise HarnessError(
            "openwindow migrations failed: "
            f"{result.stdout[-500:]} {result.stderr[-500:]}"
        )
    print("[harness] openwindow migrations applied to temp database")


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------

SERVICE_SPECS = (
    (
        "person-api",
        PERSON_API_PORT,
        f"{PERSON_API_URL}/health",
        str(PROJECT_ROOT / "services" / "person-api"),
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PERSON_API_PORT)],
        {
            "DATABASE_URL": "postgresql+asyncpg://localhost:5432/person_api",
            "SENTINEL_URL": SENTINEL_URL,
        },
    ),
    (
        "sentinel",
        SENTINEL_PORT,
        f"{SENTINEL_URL}/health",
        str(PROJECT_ROOT / "services" / "aiops-sentinel"),
        [sys.executable, "-m", "uvicorn", "app.service:app", "--host", "127.0.0.1", "--port", str(SENTINEL_PORT)],
        {
            "QWEN_BASE_URL": LLM_URL,
            "QWEN_API_KEY": "1234",
            # The user-run server rejects other model names (404 model not
            # found) — pin an id from its advertised model list.
            "QWEN_MODEL": "Qwen3.8-27B-oQ4e-mtp",
            # 27B generations under a 12-way concurrent burst need headroom
            # beyond the 15s code default.
            "QWEN_TIMEOUT": "90",
            # Thinking-by-default models otherwise take minutes per call and
            # exceed the reporter's 30s window; this server honors the flag.
            "QWEN_DISABLE_THINKING": "1",
            # Greedy decoding: identical events must yield byte-identical
            # root-cause text so openwindow's dedupe is deterministic.
            "QWEN_TEMPERATURE": "0",
            "OPENWINDOW_INGEST_URL": f"{OPENWINDOW_URL}/api/v1/incidents",
        },
    ),
    (
        "openwindow",
        OPENWINDOW_PORT,
        f"{OPENWINDOW_URL}/health/",
        str(PROJECT_ROOT / "apps" / "openwindow"),
        [sys.executable, "manage.py", "runserver", str(OPENWINDOW_PORT), "--noreload"],
        {
            "SENTINEL_CALLBACK_URL": f"{SENTINEL_URL}/api/v1/execute",
            "DJANGO_DEBUG": "true",
            "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
        },
    ),
)


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_service(name: str, port: int, cwd: str, args: list[str], extra_env: dict[str, str], db_dsn: str | None = None) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(extra_env)
    if db_dsn:
        env["DJANGO_DB_URL"] = db_dsn
        env["DJANGO_SETTINGS_MODULE"] = "openwindow.config.django_settings"
    log_path = LOG_DIR / f"{name}-{int(time.time())}.log"
    log_file = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise HarnessError(f"failed to spawn {name}: {exc}") from exc
    finally:
        log_file.close()
    print(f"[harness] started {name} on port {port} (pid {proc.pid}, log {log_path})")
    return proc


def stop_children(children: dict[str, subprocess.Popen]) -> None:
    for name, proc in children.items():
        if proc.poll() is not None:
            continue
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait(timeout=10)
        print(f"[harness] stopped {name} (pid {proc.pid})")


async def wait_ready(name: str, url: str, timeout: float = READY_TIMEOUT, interval: float = 0.5) -> tuple[bool, float]:
    started = time.monotonic()
    deadline = started + timeout
    async with httpx.AsyncClient(timeout=3.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return True, time.monotonic() - started
            except httpx.RequestError:
                pass
            await asyncio.sleep(interval)
    return False, time.monotonic() - started


def unhealthy_message(name: str, url: str, port: int, waited: float) -> str:
    return f"{name} not healthy on port {port} at {url} after {waited:.1f}s — aborting live session"


# ---------------------------------------------------------------------------
# Session fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
async def live_stack() -> AsyncGenerator[dict, None]:
    try:
        await preflight()
    except PreflightMissing as exc:
        pytest.skip(str(exc))

    db_name = await create_temp_db()
    db_dsn = target_dsn(db_name)

    children: dict[str, subprocess.Popen] = {}
    try:
        run_migrations(db_dsn)
        for name, port, _url, cwd, args, extra_env in SERVICE_SPECS:
            if port_in_use(port):
                raise HarnessError(f"port {port} already in use — cannot start {name}")
            db_dsn_arg = db_dsn if name == "openwindow" else None
            children[name] = start_service(name, port, cwd, args, extra_env, db_dsn_arg)
    except Exception:
        stop_children(children)
        await drop_temp_db(db_name)
        raise

    try:
        for name, port, url, *_ in SERVICE_SPECS:
            ok, waited = await wait_ready(name, url)
            if not ok:
                stop_children(children)
                pytest.exit(unhealthy_message(name, url, port, waited), returncode=1)
            print(f"[harness] {name} ready at {url} (health 200 in {waited:.1f}s)")
        yield {
            "person_api": PERSON_API_URL,
            "sentinel": SENTINEL_URL,
            "openwindow": OPENWINDOW_URL,
            "db_name": db_name,
            "db_dsn": db_dsn,
            "postgres_addr": POSTGRES_ADDR,
            "llm_url": LLM_URL,
            "logs": LOG_DIR,
        }
    finally:
        stop_children(children)
        await drop_temp_db(db_name)
