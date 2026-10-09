from httpx import AsyncClient


class TestCacheTelemetry:
    async def test_endpoint_returns_all_fields(self, client: AsyncClient):
        resp = await client.get("/api/v1/telemetry/cache")
        assert resp.status_code == 200
        data = resp.json()
        for field in ("hits", "misses", "entries", "ttl"):
            assert field in data, f"missing {field} in {data}"
        assert data["ttl"] == 600

    async def test_hit_and_miss_counters_move_with_traffic(self, client: AsyncClient, active_worker):
        before = (await client.get("/api/v1/telemetry/cache")).json()

        r1 = await client.get(f"/api/v1/active/workers/{active_worker.id}")
        assert r1.status_code == 200  # cache miss -> DB -> cached

        r2 = await client.get(f"/api/v1/active/workers/{active_worker.id}")
        assert r2.status_code == 200  # cache hit

        after = (await client.get("/api/v1/telemetry/cache")).json()
        assert after["misses"] == before["misses"] + 1
        assert after["hits"] == before["hits"] + 1

    async def test_404_read_does_not_increment_counters(self, client: AsyncClient):
        before = (await client.get("/api/v1/telemetry/cache")).json()

        resp = await client.get("/api/v1/active/workers/definitely-not-here")
        assert resp.status_code == 404

        after = (await client.get("/api/v1/telemetry/cache")).json()
        assert after["hits"] == before["hits"]
        assert after["misses"] == before["misses"]
