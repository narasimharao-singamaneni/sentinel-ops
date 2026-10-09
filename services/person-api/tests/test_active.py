import pytest
from httpx import AsyncClient

from app.models import Worker


class TestCreateActiveWorker:
    async def test_create_active_worker(self, client: AsyncClient, mock_semaphore):
        resp = await client.post(
            "/api/v1/active/workers",
            json={"name": "John Doe", "status": "active"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "John Doe"
        assert data["status"] == "active"
        assert "id" in data

    async def test_create_active_worker_validates_status(
        self, client: AsyncClient, mock_semaphore,
    ):
        resp = await client.post(
            "/api/v1/active/workers",
            json={"name": "Jane Doe", "status": "inactive"},
        )
        assert resp.status_code == 400

    async def test_create_active_worker_name_required(
        self, client: AsyncClient, mock_semaphore,
    ):
        resp = await client.post(
            "/api/v1/active/workers",
            json={"status": "active"},
        )
        assert resp.status_code == 422


class TestGetActiveWorker:
    async def test_get_active_worker(self, client: AsyncClient, active_worker: Worker, mock_semaphore):
        resp = await client.get(f"/api/v1/active/workers/{active_worker.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(active_worker.id)
        assert data["name"] == active_worker.name
        assert data["status"] == "active"

    async def test_get_active_worker_not_found(self, client: AsyncClient, mock_semaphore):
        resp = await client.get("/api/v1/active/workers/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_get_active_worker_cache_hit(self, client: AsyncClient, active_worker: Worker, mock_semaphore):
        resp1 = await client.get(f"/api/v1/active/workers/{active_worker.id}")
        assert resp1.status_code == 200
        resp2 = await client.get(f"/api/v1/active/workers/{active_worker.id}")
        assert resp2.status_code == 200
        assert resp1.json() == resp2.json()


class TestUpdateActiveWorker:
    async def test_update_active_worker(self, client: AsyncClient, active_worker: Worker, mock_semaphore):
        resp = await client.put(
            f"/api/v1/active/workers/{active_worker.id}",
            json={"name": "Jane Updated"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Jane Updated"

    async def test_update_active_worker_bad_status(self, client: AsyncClient, active_worker: Worker, mock_semaphore):
        resp = await client.put(
            f"/api/v1/active/workers/{active_worker.id}",
            json={"status": "inactive"},
        )
        assert resp.status_code == 400

    async def test_update_active_worker_not_found(self, client: AsyncClient, mock_semaphore):
        resp = await client.put(
            "/api/v1/active/workers/00000000-0000-0000-0000-000000000000",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404


class TestDeleteActiveWorker:
    async def test_delete_active_worker(self, client: AsyncClient, active_worker: Worker, mock_semaphore):
        resp = await client.delete(f"/api/v1/active/workers/{active_worker.id}")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Worker deleted successfully"

    async def test_delete_active_worker_not_found(self, client: AsyncClient, mock_semaphore):
        resp = await client.delete("/api/v1/active/workers/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_delete_active_worker_then_get_404(
        self, client: AsyncClient, active_worker: Worker, mock_semaphore,
    ):
        await client.delete(f"/api/v1/active/workers/{active_worker.id}")
        resp = await client.get(f"/api/v1/active/workers/{active_worker.id}")
        assert resp.status_code == 404


class TestListActiveWorkers:
    async def test_list_active_paginated(self, client: AsyncClient, seeded_active_workers, mock_semaphore):
        resp = await client.get("/api/v1/active/workers", params={"limit": 5, "offset": 0})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["workers"]) == 5
        assert data["total"] == 25

    async def test_list_active_default_limit(self, client: AsyncClient, active_worker: Worker, mock_semaphore):
        resp = await client.get("/api/v1/active/workers")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["workers"]) >= 1
        assert data["total"] >= 1

    async def test_list_active_with_offset(self, client: AsyncClient, seeded_active_workers, mock_semaphore):
        resp = await client.get("/api/v1/active/workers", params={"limit": 5, "offset": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["workers"]) == 5

    async def test_list_active_bad_limit(self, client: AsyncClient, mock_semaphore):
        resp = await client.get("/api/v1/active/workers", params={"limit": -1})
        assert resp.status_code == 400

    async def test_list_active_exceeds_max(self, client: AsyncClient, mock_semaphore):
        resp = await client.get("/api/v1/active/workers", params={"limit": 200})
        assert resp.status_code == 400