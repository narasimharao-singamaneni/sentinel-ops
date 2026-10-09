from fastapi import APIRouter

import app.middleware as middleware_module
from app import cache
from app.config import settings
from app.database import engine

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


@router.get("/pool")
async def pool_telemetry():
    pool = engine.pool
    semaphore = middleware_module._semaphore
    return {
        "pool_size": pool.size(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "concurrency_max": settings.MAX_CONCURRENT,
        "concurrency_in_use": max(0, settings.MAX_CONCURRENT - semaphore._value),
        "wait_timeout_ms": settings.SEMAPHORE_TIMEOUT * 1000,
    }


@router.get("/cache")
async def cache_telemetry():
    return cache.cache_stats()
