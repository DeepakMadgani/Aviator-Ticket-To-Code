"""SQLAlchemy ORM models for usage tracking.

Defines the declarative base and table mappings used by the usage tracking
subsystem. Tables use ``schema=None`` so SQLAlchemy's
``schema_translate_map`` can redirect them at runtime.
"""

import datetime
from datetime import date

from sqlalchemy import JSON, BigInteger, Date, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Declarative base for usage tracking ORM models."""


class UsageTransaction(Base):
    """Individual usage transaction record.

    Created in each tenant schema.  ``schema=None`` is translated via
    ``schema_translate_map`` at runtime.
    """

    __tablename__ = "usage_transactions"
    __table_args__ = (
        Index("idx_ut_created_at", "created_at"),
        Index("idx_ut_type", "transaction_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_type: Mapped[str] = mapped_column(Text, nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_total_requests: Mapped[int] = mapped_column(Integer, default=0)
    tx_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UsageDailyTally(Base):
    """Aggregated daily usage tally.

    Created in each tenant schema.
    """

    __tablename__ = "usage_daily_tallies"
    __table_args__ = (
        UniqueConstraint("tally_date", "transaction_type", name="uq_tally_date_type"),
        Index("idx_udt_date", "tally_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tally_date: Mapped[date] = mapped_column(Date, nullable=False)
    transaction_type: Mapped[str] = mapped_column(Text, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    total_documents: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_total_requests: Mapped[int] = mapped_column(Integer, default=0)
