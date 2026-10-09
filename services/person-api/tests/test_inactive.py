import pytest
from httpx import AsyncClient

from app.models import Worker


class TestCreateInactiveWorker:
    async def test_create_inactive_worker(self, client: AsyncClient, mock_semaphore):
        resp = await client.post(
            "/api/v1/inactive/workers",
            json={"name": "Former Employee", "status": "inactive"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Former Employee"
        assert data["status"] == "inactive"
        assert "id" in data

    async def test_create_inactive_worker_validates_status(
        self, client: AsyncClient, mock_semaphore,
    ):
        resp = await client.post(
            "/api/v1/inactive/workers",
            json={"name": "Jane Doe", "status": "active"},
        )
        assert resp.status_code == 400


class TestGetInactiveWorker:
    async def test_get_inactive_worker(self, client: AsyncClient, inactive_worker: Worker, mock_semaphore):
        resp = await client.get(f"/api/v1/inactive/workers/{inactive_worker.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(inactive_worker.id)
        assert data["name"] == inactive_worker.name
        assert data["status"] == "inactive"

    async def test_get_inactive_worker_not_found(self, client: AsyncClient, mock_semaphore):
        resp = await client.get("/api/v1/inactive/workers/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestUpdateInactiveWorker:
    async def test_update_inactive_worker(self, client: AsyncClient, inactive_worker: Worker, mock_semaphore):
        resp = await client.put(
            f"/api/v1/inactive/workers/{inactive_worker.id}",
            json={"name": "Former Updated"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Former Updated"

    async def test_update_inactive_worker_bad_status(self, client: AsyncClient, inactive_worker: Worker, mock_semaphore):
        resp = await client.put(
            f"/api/v1/inactive/workers/{inactive_worker.id}",
            json={"status": "active"},
        )
        assert resp.status_code == 400

    async def test_update_inactive_worker_not_found(self, client: AsyncClient, mock_semaphore):
        resp = await client.put(
            "/api/v1/inactive/workers/00000000-0000-0000-0000-000000000000",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404


class TestDeleteInactiveWorker:
    async def test_delete_inactive_worker(self, client: AsyncClient, inactive_worker: Worker, mock_semaphore):
        resp = await client.delete(f"/api/v1/inactive/workers/{inactive_worker.id}")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Worker deleted successfully"

    async def test_delete_inactive_worker_not_found(self, client: AsyncClient, mock_semaphore):
        resp = await client.delete("/api/v1/inactive/workers/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestListInactiveWorkers:
    async def test_list_inactive_paginated(self, client: AsyncClient, seeded_inactive_workers, mock_semaphore):
        resp = await client.get("/api/v1/inactive/workers", params={"limit": 5, "offset": 0})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["workers"]) == 5
        assert data["total"] == 25

    async def test_list_inactive_default_limit(self, client: AsyncClient, inactive_worker: Worker, mock_semaphore):
        resp = await client.get("/api/v1/inactive/workers")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["workers"]) >= 1
        assert data["total"] >= 1