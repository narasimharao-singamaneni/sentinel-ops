"""+v: the live harness brings up all three services healthy on a fresh temp openwindow DB."""

import asyncpg
import httpx

import conftest as harness


async def test_all_three_services_ready(live_stack: dict):
    async with httpx.AsyncClient(timeout=5.0) as client:
        person_api = await client.get(f"{live_stack['person_api']}/health")
        sentinel = await client.get(f"{live_stack['sentinel']}/health")
        openwindow = await client.get(f"{live_stack['openwindow']}/health/")

    assert person_api.status_code == 200, f"person-api health: {person_api.text}"
    assert sentinel.status_code == 200, f"sentinel health: {sentinel.text}"
    assert openwindow.status_code == 200, f"openwindow health: {openwindow.text}"
    assert person_api.json().get("status") == "healthy"
    assert openwindow.json().get("status") == "healthy"


async def test_temp_db_created_and_migrated(live_stack: dict):
    db_name = live_stack["db_name"]
    assert db_name.startswith("openwindow_test_")

    conn = await asyncpg.connect(harness.admin_dsn(), timeout=5.0)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        assert exists == 1, f"temp database {db_name} missing before tests ran"
    finally:
        await conn.close()

    db_conn = await asyncpg.connect(live_stack["db_dsn"], timeout=5.0)
    try:
        migrations = await db_conn.fetchval("SELECT count(*) FROM django_migrations")
        assert migrations >= 3, f"expected >=3 applied migrations, found {migrations}"
        table = await db_conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'incidents_incident'"
        )
        assert table == 1, "incidents_incident table missing after migrations"
    finally:
        await db_conn.close()


async def test_dashboard_data_endpoint_on_fresh_temp_db(live_stack: dict):
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{live_stack['openwindow']}/dashboard/data/")

    assert response.status_code == 200
    data = response.json()
    for key in ("total", "new", "approved", "executed", "duplicate"):
        assert isinstance(data["counts"][key], int), f"counts.{key} missing or non-int: {data['counts']}"
    assert isinstance(data["totals"]["time_saved_minutes"], (int, float))
    assert isinstance(data["incidents"], list)
    if data["counts"]["total"] == 0:
        assert data["totals"]["time_saved_minutes"] == 0
        assert data["incidents"] == []
