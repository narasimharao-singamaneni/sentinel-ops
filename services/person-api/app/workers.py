import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.cache import (
    get_worker,
    set_worker,
    invalidate_worker,
    record_cache_hit,
    record_cache_miss,
)
from app.config import settings
from app.database import get_session
from app.models import Worker
from app.schemas import WorkerCreate, WorkerUpdate, WorkerOut, WorkerListResponse

logger = logging.getLogger(__name__)


def create_worker_router(worker_status: str, *, cache_enabled: bool = False, default_limit: int = 20) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workers",
        response_model=WorkerOut,
        status_code=status.HTTP_201_CREATED,
        summary=f"Create a {worker_status} worker",
    )
    async def create_worker(worker_in: WorkerCreate, session: AsyncSession = Depends(get_session)):
        if worker_in.status != worker_status:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Status must be '{worker_status}' for this endpoint",
            )
        worker = Worker(id=str(uuid.uuid4()), name=worker_in.name, status=worker_status)
        session.add(worker)
        try:
            await session.flush()
            await session.refresh(worker)
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Worker with the requested data already exists",
            )

        out = WorkerOut(**worker.to_dict())
        return out

    @router.get("/workers/{worker_id}", response_model=WorkerOut, summary=f"Get a {worker_status} worker")
    async def get_worker_by_id(worker_id: str, session: AsyncSession = Depends(get_session)):
        if cache_enabled:
            cache_key = f"worker:{worker_status}:{worker_id}"
            cached = await get_worker(cache_key)
            if cached:
                record_cache_hit()
                return WorkerOut(**cached)

        stmt = select(Worker).where(Worker.id == worker_id, Worker.status == worker_status)
        result = await session.execute(stmt)
        worker = result.scalar_one_or_none()

        if not worker:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")

        data = worker.to_dict()
        out = WorkerOut(**data)

        if cache_enabled:
            cache_key = f"worker:{worker_status}:{worker_id}"
            record_cache_miss()
            await set_worker(cache_key, out.model_dump(mode="json"), ttl=settings.CACHE_TTL)

        return out

    @router.put("/workers/{worker_id}", response_model=WorkerOut, summary=f"Update a {worker_status} worker")
    async def update_worker(worker_id: str, worker_in: WorkerUpdate, session: AsyncSession = Depends(get_session)):
        stmt = select(Worker).where(Worker.id == worker_id, Worker.status == worker_status)
        result = await session.execute(stmt)
        worker = result.scalar_one_or_none()

        if not worker:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")

        if worker_in.name is not None:
            worker.name = worker_in.name
        if worker_in.status is not None:
            if worker_in.status != worker_status:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Status must be '{worker_status}' for this endpoint",
                )
            worker.status = worker_in.status

        try:
            await session.flush()
            await session.refresh(worker)
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Update caused a conflict",
            )

        if cache_enabled:
            cache_key = f"worker:{worker_status}:{worker_id}"
            await invalidate_worker(cache_key)

        out = WorkerOut(**worker.to_dict())
        return out

    @router.delete("/workers/{worker_id}", summary=f"Delete a {worker_status} worker")
    async def delete_worker(worker_id: str, session: AsyncSession = Depends(get_session)):
        stmt = select(Worker).where(Worker.id == worker_id, Worker.status == worker_status)
        result = await session.execute(stmt)
        worker = result.scalar_one_or_none()

        if not worker:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")

        await session.delete(worker)
        await session.flush()

        if cache_enabled:
            cache_key = f"worker:{worker_status}:{worker_id}"
            await invalidate_worker(cache_key)

        return {"message": "Worker deleted successfully"}

    @router.get("/workers", response_model=WorkerListResponse, summary=f"List {worker_status} workers")
    async def list_workers(
        limit: int = default_limit,
        offset: int = 0,
        session: AsyncSession = Depends(get_session),
    ):
        if limit <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Limit must be greater than 0",
            )
        if offset < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Offset must be non-negative",
            )
        max_limit = settings.MAX_PAGE_SIZE
        if limit > max_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Limit cannot exceed {max_limit}",
            )

        count_stmt = select(func.count()).select_from(Worker).where(Worker.status == worker_status)
        count_result = await session.execute(count_stmt)
        total = count_result.scalar()

        data_stmt = (
            select(Worker)
            .where(Worker.status == worker_status)
            .order_by(Worker.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(data_stmt)
        workers = result.scalars().all()

        out_workers = [WorkerOut(**w.to_dict()) for w in workers]

        return WorkerListResponse(workers=out_workers, total=total)

    return router