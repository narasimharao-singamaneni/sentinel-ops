import asyncio
import time

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Worker


class TestConcurrencyLimiting:

    async def _fire_gets(self, client: AsyncClient, n: int, url: str) -> list:
        async def do_get():
            return await client.get(url)
        tasks = [do_get() for _ in range(n)]
        return await asyncio.gather(*tasks)

    async def test_ten_concurrent_all_succeed(self, client: AsyncClient, concurrency_semaphore, db_session: AsyncSession):
        responses = await self._fire_gets(client, 10, "/test/delay?delay=0.01")
        ok = [r for r in responses if r.status_code == 200]
        assert len(ok) == 10

    async def test_eleven_concurrent_one_rejected(self, client: AsyncClient, concurrency_semaphore, db_session: AsyncSession):
        responses = await self._fire_gets(client, 11, "/test/delay?delay=0.2")
        ok = [r for r in responses if r.status_code == 200]
        rejected = [r for r in responses if r.status_code == 503]
        assert len(ok) == 10
        assert len(rejected) == 1

    async def test_fifteen_concurrent_five_rejected(self, client: AsyncClient, concurrency_semaphore, db_session: AsyncSession):
        responses = await self._fire_gets(client, 15, "/test/delay?delay=0.2")
        ok = [r for r in responses if r.status_code == 200]
        rejected = [r for r in responses if r.status_code == 503]
        assert len(ok) == 10
        assert len(rejected) == 5

    async def test_concurrent_mixed_methods(self, client: AsyncClient, active_worker: Worker, concurrency_semaphore, db_session: AsyncSession):
        async def _post(name_suffix):
            return await client.post(
                "/api/v1/active/workers",
                json={"name": f"mixed_{name_suffix}", "status": "active"},
            )

        async def _get():
            return await client.get(f"/api/v1/active/workers/{active_worker.id}")

        tasks = [
            _post(i) for i in range(3)
        ] + [
            _get() for _ in range(4)
        ] + [
            client.delete(f"/api/v1/active/workers/{active_worker.id}") for _ in range(2)
        ]
        results = await asyncio.gather(*tasks)
        ok = [r for r in results if r.status_code in (200, 201)]
        assert len(ok) >= 6

    async def test_semaphore_releases_after_completion(
        self, client: AsyncClient, concurrency_semaphore, db_session: AsyncSession,
    ):
        batch1 = await self._fire_gets(client, 10, "/test/delay?delay=0.01")
        ok1 = [r for r in batch1 if r.status_code == 200]
        assert len(ok1) == 10

        batch2 = await self._fire_gets(client, 10, "/test/delay?delay=0.01")
        ok2 = [r for r in batch2 if r.status_code == 200]
        assert len(ok2) == 10

    async def test_no_request_starvation(self, client: AsyncClient, concurrency_semaphore, db_session: AsyncSession):
        start = time.perf_counter()
        responses = await self._fire_gets(client, 20, "/test/delay?delay=0.01")
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0
        no_500 = all(r.status_code != 500 for r in responses)
        assert no_500

    async def test_concurrent_create_no_conflicts(self, client: AsyncClient, concurrency_semaphore, db_session: AsyncSession):
        tasks = [
            client.post(
                "/api/v1/active/workers",
                json={"name": f"parallel_{i}", "status": "active"},
            )
            for i in range(10)
        ]
        results = await asyncio.gather(*tasks)
        ok = [r for r in results if r.status_code in (201, 503)]
        assert len(ok) == 10