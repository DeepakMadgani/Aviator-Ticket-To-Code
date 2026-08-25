"""SQLAlchemy ORM model for the migration state table."""

from __future__ import annotations

import datetime  # noqa: TC003 — required at runtime by SQLAlchemy mapped annotations

from sqlalchemy import BigInteger, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for migration ORM models."""


class MigrationState(Base):
    """Tracks migration checkpoint progress."""

    __tablename__ = "migration_state"

    id: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("'csai_to_adt'"))
    last_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'in_progress'"))
    total_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    migrated: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    started_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime.datetime | None] = mapped_column(server_default=text("NOW()"))
