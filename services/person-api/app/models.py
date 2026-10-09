import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, String, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Worker(Base):
    __tablename__ = "workers"
    __mapper_args__ = {"confirm_deleted_rows": False}

    id = Column(
        String(36),  # stores UUID as text for PostgreSQL/SQLite compatibility
        primary_key=True,
        default=lambda: str(_uuid.uuid4()),
    )
    name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, server_default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_workers_status", "status"),
        Index("idx_workers_name", "name"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }