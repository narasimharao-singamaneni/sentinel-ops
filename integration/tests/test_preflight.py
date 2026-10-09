"""-v: preflight skips name exact addresses; an unhealthy service aborts the session
naming the service and port, and all child processes are stopped on abort.
"""

import subprocess
import sys

import pytest

import conftest as harness


async def test_llm_preflight_unreachable_names_address():
    with pytest.raises(harness.PreflightMissing) as excinfo:
        await harness.check_llm("http://127.0.0.1:9/v1", timeout=2.0)
    assert "http://127.0.0.1:9/v1" in str(excinfo.value)


async def test_postgres_preflight_unreachable_names_address():
    with pytest.raises(harness.PreflightMissing) as excinfo:
        await harness.check_postgres("localhost:9", timeout=2.0)
    assert "localhost:9" in str(excinfo.value)


def test_preflight_defaults_are_the_canonical_addresses():
    assert harness.LLM_URL == "http://127.0.0.1:8000/v1"
    assert harness.POSTGRES_ADDR == "localhost:5432"
    assert harness.check_llm.__defaults__[0] == harness.LLM_URL
    assert harness.check_postgres.__defaults__[0] == harness.POSTGRES_ADDR


async def test_unhealthy_service_abort_names_service_and_port_and_stops_children():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert child.poll() is None

        ok, waited = await harness.wait_ready("person-api", "http://127.0.0.1:9/health", timeout=1.5)
        assert ok is False, "a closed port must never report ready"

        message = harness.unhealthy_message("person-api", "http://127.0.0.1:9/health", 9, waited)
        assert "person-api" in message
        assert "9" in message

        harness.stop_children({"person-api": child})
        child.wait(timeout=10)
        assert child.poll() is not None, "child process must be stopped on abort"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
