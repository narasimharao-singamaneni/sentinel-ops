from datetime import datetime

from pydantic import BaseModel, Field


class WorkerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    status: str = Field(..., pattern="^(active|inactive)$")


class WorkerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    status: str = Field(..., pattern="^(active|inactive)$")


class WorkerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    status: str | None = Field(None, pattern="^(active|inactive)$")


class WorkerOut(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkerListResponse(BaseModel):
    workers: list[WorkerOut]
    total: int


class ErrorDetail(BaseModel):
    detail: str