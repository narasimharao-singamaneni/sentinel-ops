import asyncio

from httpx import AsyncClient


class TestPoolTelemetry:
    async def test_endpoint_returns_all_fields(self, client: AsyncClient):
        resp = await client.get("/api/v1/telemetry/pool")
        assert resp.status_code == 200
        data = resp.json()
        for field in (
            "pool_size",
            "checked_out",
            "overflow",
            "concurrency_max",
            "concurrency_in_use",
            "wait_timeout_ms",
        ):
            assert field in data, f"missing {field} in {data}"
        assert data["concurrency_max"] == 10
        assert data["wait_timeout_ms"] == 3000

    async def test_in_use_tracks_in_flight_requests(self, client: AsyncClient):
        async def delayed():
            return await client.get("/test/delay?delay=1.0", timeout=10)

        async def read_pool_after():
            await asyncio.sleep(0.3)
            return await client.get("/api/v1/telemetry/pool")

        delayed_results, pool_resp = await asyncio.gather(
            asyncio.gather(*[delayed() for _ in range(5)], return_exceptions=True),
            read_pool_after(),
        )
        data = pool_resp.json()
        assert data["concurrency_in_use"] == 5, data
        assert all(r.status_code == 200 for r in delayed_results)

    async def test_in_use_returns_to_zero_after_rejected_storm_drains(
        self, client: AsyncClient, concurrency_semaphore
    ):
        async def fire():
            return await client.get("/test/delay?delay=0.3", timeout=10)

        responses = await asyncio.gather(*[fire() for _ in range(15)])
        assert any(r.status_code == 503 for r in responses)
        assert all(r.status_code in (200, 503) for r in responses)

        resp = await client.get("/api/v1/telemetry/pool")
        assert resp.status_code == 200
        assert resp.json()["concurrency_in_use"] == 0, "semaphore leak after storm"
