"""Functions to record usage transactions.

All database interaction goes through SQLAlchemy ORM sessions obtained from
:pymod:`aviator.services.usage_tracking.db`.  The daily tally aggregation
(previously handled by a PostgreSQL trigger) is now performed in application
code, making it database-agnostic.
"""

import asyncio
import datetime
import logging

from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from aviator.services.usage_tracking.db import usage_tracking_db
from aviator.services.usage_tracking.metrics import usage_transactions_total
from aviator.services.usage_tracking.orm import UsageDailyTally, UsageTransaction
from aviator.settings import settings

tracer = trace.get_tracer(__name__)

logger = logging.getLogger(__name__)


@tracer.start_as_current_span("upsert_tally_sync")
def _upsert_tally_sync(  # noqa: ANN202
    session: Session,
    today: datetime.date,
    transaction_type: str,
    document_count: int,
    chunk_count: int,
    input_tokens: int,
    output_tokens: int,
    llm_total_requests: int,
):
    """Increment (or create) the daily tally row for the given type/date.

    Uses SELECT … FOR UPDATE with a retry on IntegrityError to handle
    the race where two transactions both see no existing row and try
    to INSERT concurrently.
    """
    for _attempt in range(2):
        tally = session.execute(
            select(UsageDailyTally)
            .where(
                UsageDailyTally.tally_date == today,
                UsageDailyTally.transaction_type == transaction_type,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if tally:
            tally.total_count += 1
            tally.total_documents += document_count
            tally.total_chunks += chunk_count
            tally.input_tokens += input_tokens
            tally.output_tokens += output_tokens
            tally.llm_total_requests += llm_total_requests
            return

        try:
            session.add(
                UsageDailyTally(
                    tally_date=today,
                    transaction_type=transaction_type,
                    total_count=1,
                    total_documents=document_count,
                    total_chunks=chunk_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    llm_total_requests=llm_total_requests,
                )
            )
            session.flush()
        except IntegrityError:
            session.rollback()
            # Another transaction inserted the row — retry to UPDATE it
            continue
        else:
            return
    logger.warning("Failed to upsert tally after retries (date=%s, type=%s)", today, transaction_type)


@tracer.start_as_current_span("upsert_tally_async")
async def _upsert_tally_async(  # noqa: ANN202
    session: AsyncSession,
    today: datetime.date,
    transaction_type: str,
    document_count: int,
    chunk_count: int,
    input_tokens: int,
    output_tokens: int,
    llm_total_requests: int,
):
    """Async version of the daily tally upsert.

    Retries once on IntegrityError to handle concurrent first-of-day inserts.
    """
    for _attempt in range(2):
        result = await session.execute(
            select(UsageDailyTally)
            .where(
                UsageDailyTally.tally_date == today,
                UsageDailyTally.transaction_type == transaction_type,
            )
            .with_for_update()
        )
        tally = result.scalar_one_or_none()

        if tally:
            tally.total_count += 1
            tally.total_documents += document_count
            tally.total_chunks += chunk_count
            tally.input_tokens += input_tokens
            tally.output_tokens += output_tokens
            tally.llm_total_requests += llm_total_requests
            return

        try:
            session.add(
                UsageDailyTally(
                    tally_date=today,
                    transaction_type=transaction_type,
                    total_count=1,
                    total_documents=document_count,
                    total_chunks=chunk_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    llm_total_requests=llm_total_requests,
                )
            )
            await session.flush()
        except IntegrityError:
            await session.rollback()
            continue
        else:
            return
    logger.warning("Failed to upsert tally after retries (date=%s, type=%s)", today, transaction_type)


@tracer.start_as_current_span("record_transaction")
async def record_transaction(
    tenant_id: str | None,
    transaction_type: str,
    document_count: int = 0,
    chunk_count: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    llm_total_requests: int = 0,
    metadata: dict | None = None,
) -> None:
    """Record a usage transaction asynchronously.

    This is a no-op when usage tracking is disabled.
    Errors are logged but never propagated to avoid breaking the caller.
    """
    if not settings.usage_tracking_enabled:
        return

    try:
        if usage_tracking_db.use_sync_fallback():
            await asyncio.to_thread(
                record_transaction_sync,
                tenant_id,
                transaction_type,
                document_count,
                chunk_count,
                input_tokens,
                output_tokens,
                llm_total_requests,
                metadata,
            )
            return

        schema = await usage_tracking_db.ensure_tenant_schema(tenant_id)
        today = datetime.datetime.now(tz=datetime.UTC).date()

        async with usage_tracking_db.async_session(schema) as session, session.begin():
            session.add(
                UsageTransaction(
                    transaction_type=transaction_type,
                    document_count=document_count,
                    chunk_count=chunk_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    llm_total_requests=llm_total_requests,
                    tx_metadata=metadata or {},
                )
            )
            await _upsert_tally_async(
                session,
                today,
                transaction_type,
                document_count,
                chunk_count,
                input_tokens,
                output_tokens,
                llm_total_requests,
            )

        usage_transactions_total.labels(
            tenant_id=tenant_id or "default",
            transaction_type=transaction_type,
        ).inc()
    except Exception:
        logger.exception(
            "Failed to record usage transaction (type=%s, tenant=%s)",
            transaction_type,
            tenant_id,
        )


@tracer.start_as_current_span("record_transaction_sync")
def record_transaction_sync(
    tenant_id: str | None,
    transaction_type: str,
    document_count: int = 0,
    chunk_count: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    llm_total_requests: int = 0,
    metadata: dict | None = None,
) -> None:
    """Record a usage transaction synchronously (for use in Celery tasks).

    This is a no-op when usage tracking is disabled.
    Errors are logged but never propagated to avoid breaking the caller.
    """
    if not settings.usage_tracking_enabled:
        return

    try:
        schema = usage_tracking_db.ensure_tenant_schema_sync(tenant_id)
        today = datetime.datetime.now(tz=datetime.UTC).date()

        with usage_tracking_db.sync_session(schema) as session, session.begin():
            session.add(
                UsageTransaction(
                    transaction_type=transaction_type,
                    document_count=document_count,
                    chunk_count=chunk_count,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    llm_total_requests=llm_total_requests,
                    tx_metadata=metadata or {},
                )
            )
            _upsert_tally_sync(
                session,
                today,
                transaction_type,
                document_count,
                chunk_count,
                input_tokens,
                output_tokens,
                llm_total_requests,
            )

        usage_transactions_total.labels(
            tenant_id=tenant_id or "default",
            transaction_type=transaction_type,
        ).inc()
    except Exception:
        logger.exception(
            "Failed to record usage transaction sync (type=%s, tenant=%s)",
            transaction_type,
            tenant_id,
        )
