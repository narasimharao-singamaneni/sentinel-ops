import pytest
from httpx import AsyncClient

from app.models import Worker


class TestFactoryIsolation:
    async def test_create_active_not_visible_in_inactive(
        self, client: AsyncClient, mock_semaphore,
    ):
        resp = await client.post(
            "/api/v1/active/workers",
            json={"name": "Isolation Test", "status": "active"},
        )
        assert resp.status_code == 201
        worker_id = resp.json()["id"]

        resp = await client.get(f"/api/v1/inactive/workers/{worker_id}")
        assert resp.status_code == 404

    async def test_create_inactive_not_visible_in_active(
        self, client: AsyncClient, mock_semaphore,
    ):
        resp = await client.post(
            "/api/v1/inactive/workers",
            json={"name": "Isolation Test", "status": "inactive"},
        )
        assert resp.status_code == 201
        worker_id = resp.json()["id"]

        resp = await client.get(f"/api/v1/active/workers/{worker_id}")
        assert resp.status_code == 404

    async def test_list_active_excludes_inactive(
        self, client: AsyncClient, active_worker: Worker, inactive_worker: Worker, mock_semaphore,
    ):
        resp = await client.get("/api/v1/active/workers")
        assert resp.status_code == 200
        data = resp.json()
        ids = [w["id"] for w in data["workers"]]
        assert str(active_worker.id) in ids
        assert str(inactive_worker.id) not in ids

    async def test_list_inactive_excludes_active(
        self, client: AsyncClient, active_worker: Worker, inactive_worker: Worker, mock_semaphore,
    ):
        resp = await client.get("/api/v1/inactive/workers")
        assert resp.status_code == 200
        data = resp.json()
        ids = [w["id"] for w in data["workers"]]
        assert str(inactive_worker.id) in ids
        assert str(active_worker.id) not in ids


class TestFactoryRouteStructure:
    async def test_active_has_five_routes(self, client: AsyncClient, mock_semaphore):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        active_paths = {k: v for k, v in paths.items() if "/active/workers" in k or "/active/" in k}
        route_count = sum(len(methods) for methods in active_paths.values())
        assert route_count == 5

    async def test_inactive_has_five_routes(self, client: AsyncClient, mock_semaphore):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        inactive_paths = {k: v for k, v in paths.items() if "/inactive/workers" in k or "/inactive/" in k}
        route_count = sum(len(methods) for methods in inactive_paths.values())
        assert route_count == 5