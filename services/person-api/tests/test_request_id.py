import asyncio
import uuid

import pytest
from httpx import AsyncClient


class TestRequestId:
    async def test_response_carries_generated_request_id(self, client: AsyncClient):
        resp = await client.get("/api/v1/active/workers?limit=1")
        assert resp.status_code == 200
        rid = resp.headers.get("x-request-id")
        assert rid is not None and rid != ""
        parsed = uuid.UUID(rid)
        assert str(parsed) == rid

    async def test_consecutive_requests_get_different_ids(self, client: AsyncClient):
        r1 = await client.get("/health")
        r2 = await client.get("/health")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

    async def test_inbound_request_id_honored(self, client: AsyncClient):
        inbound = f"inbound-{uuid.uuid4()}"
        resp = await client.get("/health", headers={"X-Request-ID": inbound})
        assert resp.status_code == 200
        assert resp.headers["x-request-id"] == inbound

    async def test_503_rejection_carries_request_id(self, client: AsyncClient, concurrency_semaphore):
        async def fire():
            return await client.get("/test/delay?delay=0.3", timeout=10)

        responses = await asyncio.gather(*[fire() for _ in range(12)])
        assert any(r.status_code == 503 for r in responses)
        for r in responses:
            assert r.headers.get("x-request-id"), f"status {r.status_code} missing X-Request-ID"
