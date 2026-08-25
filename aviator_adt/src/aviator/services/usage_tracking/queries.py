"""Functions to query usage stats and semantic size.

All queries use SQLAlchemy ORM sessions from
:pymod:`aviator.services.usage_tracking.db`.  Monthly and yearly aggregation
is performed in Python rather than database-specific SQL functions, keeping
the implementation database-agnostic.
"""

import asyncio
import logging
from datetime import date

from sqlalchemy import func, select

from aviator.services.tenant import tenant_id_to_schema_name
from aviator.services.usage_tracking.db import usage_tracking_db
from aviator.services.usage_tracking.models import DailyTallyModel, SemanticSizeModel
from aviator.services.usage_tracking.orm import UsageDailyTally
from aviator.settings import settings

logger = logging.getLogger(__name__)


def _resolve_schema(tenant_id: str | None) -> str:
    """Derive the schema name for a tenant deterministically."""
    if not tenant_id:
        return settings.default_schema
    return tenant_id_to_schema_name(tenant_id)


def _aggregate_tallies(tallies: list[DailyTallyModel], units: str) -> list[DailyTallyModel]:
    """Aggregate daily tallies into monthly or yearly buckets in Python."""
    aggregated: dict[tuple[str, str], DailyTallyModel] = {}

    for t in tallies:
        if units == "months":
            period = t.date[:7]  # "YYYY-MM"
        elif units == "years":
            period = t.date[:4]  # "YYYY"
        else:
            period = t.date

        key = (period, t.transaction_type)
        if key not in aggregated:
            aggregated[key] = DailyTallyModel(
                date=period,
                transaction_type=t.transaction_type,
                total_count=0,
                total_documents=0,
                total_chunks=0,
                input_tokens=0,
                output_tokens=0,
                llm_total_requests=0,
            )

        agg = aggregated[key]
        agg.total_count += t.total_count
        agg.total_documents += t.total_documents
        agg.total_chunks += t.total_chunks
        agg.input_tokens += t.input_tokens
        agg.output_tokens += t.output_tokens
        agg.llm_total_requests += t.llm_total_requests

    return sorted(aggregated.values(), key=lambda x: (x.date, x.transaction_type))


async def get_usage_stats(
    tenant_id: str | None,
    from_date: date,
    to_date: date,
    units: str = "days",
) -> list[DailyTallyModel]:
    """Query usage stats for a tenant over a date range.

    Args:
        tenant_id: The tenant identifier (None for public).
        from_date: Inclusive start date.
        to_date: Inclusive end date.
        units: Aggregation granularity — ``"days"``, ``"months"``, or ``"years"``.

    Returns:
        A list of tally records, aggregated to the requested granularity.

    """
    if usage_tracking_db.use_sync_fallback():
        return await asyncio.to_thread(_get_usage_stats_sync, tenant_id, from_date, to_date, units)

    schema = _resolve_schema(tenant_id)

    async with usage_tracking_db.async_session(schema) as session:
        result = await session.execute(
            select(UsageDailyTally)
            .where(UsageDailyTally.tally_date.between(from_date, to_date))
            .order_by(UsageDailyTally.tally_date, UsageDailyTally.transaction_type)
        )
        rows = result.scalars().all()

    # Convert to Pydantic models
    daily = [
        DailyTallyModel(
            date=str(row.tally_date),
            transaction_type=row.transaction_type,
            total_count=row.total_count,
            total_documents=row.total_documents,
            total_chunks=row.total_chunks,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            llm_total_requests=row.llm_total_requests,
        )
        for row in rows
    ]

    if units == "days":
        return daily

    if units not in ("months", "years"):
        msg = f"Unsupported units: {units}"
        raise ValueError(msg)

    return _aggregate_tallies(daily, units)


def _get_usage_stats_sync(
    tenant_id: str | None,
    from_date: date,
    to_date: date,
    units: str = "days",
) -> list[DailyTallyModel]:
    """Sync variant of usage stats query for Windows fallback."""
    schema = _resolve_schema(tenant_id)

    with usage_tracking_db.sync_session(schema) as session:
        result = session.execute(
            select(UsageDailyTally)
            .where(UsageDailyTally.tally_date.between(from_date, to_date))
            .order_by(UsageDailyTally.tally_date, UsageDailyTally.transaction_type)
        )
        rows = result.scalars().all()

    daily = [
        DailyTallyModel(
            date=str(row.tally_date),
            transaction_type=row.transaction_type,
            total_count=row.total_count,
            total_documents=row.total_documents,
            total_chunks=row.total_chunks,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            llm_total_requests=row.llm_total_requests,
        )
        for row in rows
    ]

    if units == "days":
        return daily

    if units not in ("months", "years"):
        msg = f"Unsupported units: {units}"
        raise ValueError(msg)

    return _aggregate_tallies(daily, units)


async def get_semantic_size(
    tenant_id: str | None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> SemanticSizeModel:
    """Compute semantic size metrics from usage daily tallies.

    Sums documents and chunks from ``embedding_add`` tallies, subtracting
    ``embedding_delete`` totals, to produce an approximate count without
    scanning the vector store table.  ``embedding_update`` only contributes
    chunk changes (the document count is unchanged by a replacement).

    Args:
        tenant_id: The tenant identifier (None for public/default).
        from_date: Optional inclusive start date to scope the calculation.
        to_date: Optional inclusive end date to scope the calculation.

    Returns:
        A :class:`SemanticSizeModel` with document and chunk counts.

    """
    try:
        if usage_tracking_db.use_sync_fallback():
            return await asyncio.to_thread(_get_semantic_size_sync, tenant_id, from_date, to_date)

        schema = _resolve_schema(tenant_id)

        async with usage_tracking_db.async_session(schema) as session:
            query = (
                select(
                    UsageDailyTally.transaction_type,
                    func.coalesce(func.sum(UsageDailyTally.total_documents), 0),
                    func.coalesce(func.sum(UsageDailyTally.total_chunks), 0),
                )
                .where(UsageDailyTally.transaction_type.in_(["embedding_add", "embedding_update", "embedding_delete"]))
                .group_by(UsageDailyTally.transaction_type)
            )
            if from_date is not None and to_date is not None:
                query = query.where(UsageDailyTally.tally_date.between(from_date, to_date))
            result = await session.execute(query)
            rows = result.all()

        total_documents = 0
        total_chunks = 0
        for tx_type, docs, chunks in rows:
            if tx_type == "embedding_add":
                total_documents += docs
                total_chunks += chunks
            elif tx_type == "embedding_update":
                # The recorded chunk_count is already the net delta
                # (new_chunks - old_chunks); the document count will increase
                # since an update requires the same cost as a single document.
                total_documents += docs
                total_chunks += chunks
            elif tx_type == "embedding_delete":
                total_documents -= docs
                total_chunks -= chunks

        return SemanticSizeModel(
            total_documents=max(total_documents, 0),
            total_chunks=max(total_chunks, 0),
        )

    except Exception:
        logger.exception("Failed to get semantic size for tenant=%s", tenant_id)

    return SemanticSizeModel()


def _get_semantic_size_sync(
    tenant_id: str | None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> SemanticSizeModel:
    """Sync variant of semantic size query for Windows fallback."""
    try:
        schema = _resolve_schema(tenant_id)

        with usage_tracking_db.sync_session(schema) as session:
            query = (
                select(
                    UsageDailyTally.transaction_type,
                    func.coalesce(func.sum(UsageDailyTally.total_documents), 0),
                    func.coalesce(func.sum(UsageDailyTally.total_chunks), 0),
                )
                .where(UsageDailyTally.transaction_type.in_(["embedding_add", "embedding_update", "embedding_delete"]))
                .group_by(UsageDailyTally.transaction_type)
            )
            if from_date is not None and to_date is not None:
                query = query.where(UsageDailyTally.tally_date.between(from_date, to_date))
            rows = session.execute(query).all()

        total_documents = 0
        total_chunks = 0
        for tx_type, docs, chunks in rows:
            if tx_type == "embedding_add":
                total_documents += docs
                total_chunks += chunks
            elif tx_type == "embedding_update":
                total_chunks += chunks
            elif tx_type == "embedding_delete":
                total_documents -= docs
                total_chunks -= chunks

        return SemanticSizeModel(
            total_documents=max(total_documents, 0),
            total_chunks=max(total_chunks, 0),
        )

    except Exception:
        logger.exception("Failed to get semantic size for tenant=%s", tenant_id)

    return SemanticSizeModel()
