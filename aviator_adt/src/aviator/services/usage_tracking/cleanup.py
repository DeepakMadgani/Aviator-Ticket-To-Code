"""Retention cleanup for usage transaction and tally rows.

All database interaction uses SQLAlchemy ORM sessions from
:pymod:`aviator.services.usage_tracking.db`, keeping the implementation
database-agnostic.
"""

import datetime
import logging

from opentelemetry import trace
from sqlalchemy import delete, select, text

from aviator.services.usage_tracking.db import usage_tracking_db
from aviator.services.usage_tracking.orm import UsageDailyTally, UsageTransaction
from aviator.settings import settings

tracer = trace.get_tracer(__name__)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10_000


@tracer.start_as_current_span("get_all_schemas")
def _get_all_schemas() -> list[str]:
    """Return all tenant schema names plus the default schema.

    Discovers tenant schemas by querying ``information_schema.schemata``
    for schemas matching the configured ``tenant_schema_prefix``.
    """
    prefix = settings.tenant_schema_prefix
    with usage_tracking_db.sync_session() as session:
        result = session.execute(
            text("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE :pattern"),
            {"pattern": f"{prefix}%"},
        )
        schemas = [row[0] for row in result.all()]
    schemas.append(settings.default_schema)
    return schemas


@tracer.start_as_current_span("cleanup_old_transactions")
def cleanup_old_transactions() -> int:
    """Delete usage_transactions rows older than the retention period.

    Iterates all tenant schemas (discovered via ``information_schema``)
    plus the default schema, deleting in batches to avoid long locks.

    Returns:
        Total number of rows deleted across all schemas.

    """
    retention_days = settings.usage_tracking_retention_days
    cutoff = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=retention_days)
    total_deleted = 0

    for schema in _get_all_schemas():
        deleted_in_schema = 0
        while True:
            with usage_tracking_db.sync_session(schema) as session:
                ids = (
                    session.execute(
                        select(UsageTransaction.id).where(UsageTransaction.created_at < cutoff).limit(_BATCH_SIZE)
                    )
                    .scalars()
                    .all()
                )

                if not ids:
                    break

                result = session.execute(delete(UsageTransaction).where(UsageTransaction.id.in_(ids)))
                session.commit()
                batch_count = result.rowcount
                deleted_in_schema += batch_count

                if batch_count < _BATCH_SIZE:
                    break

        if deleted_in_schema > 0:
            logger.info(
                "Cleaned up %d old transactions from schema '%s'",
                deleted_in_schema,
                schema,
            )
        total_deleted += deleted_in_schema

    return total_deleted


@tracer.start_as_current_span("cleanup_old_tallies")
def cleanup_old_tallies() -> int:
    """Delete usage_daily_tallies rows older than the tally retention period.

    Iterates all tenant schemas (discovered via ``information_schema``)
    plus the default schema, deleting in batches to avoid long locks.

    If ``usage_tracking_tally_retention_days`` is ``0``, tallies are kept
    indefinitely and this function is a no-op.

    Returns:
        Total number of rows deleted across all schemas.

    """
    retention_days = settings.usage_tracking_tally_retention_days
    if retention_days == 0:
        logger.debug("Tally retention disabled (set to 0), skipping cleanup")
        return 0

    cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=retention_days)).date()
    total_deleted = 0

    for schema in _get_all_schemas():
        deleted_in_schema = 0
        while True:
            with usage_tracking_db.sync_session(schema) as session:
                ids = (
                    session.execute(
                        select(UsageDailyTally.id).where(UsageDailyTally.tally_date < cutoff).limit(_BATCH_SIZE)
                    )
                    .scalars()
                    .all()
                )

                if not ids:
                    break

                result = session.execute(delete(UsageDailyTally).where(UsageDailyTally.id.in_(ids)))
                session.commit()
                batch_count = result.rowcount
                deleted_in_schema += batch_count

                if batch_count < _BATCH_SIZE:
                    break

        if deleted_in_schema > 0:
            logger.info(
                "Cleaned up %d old tally rows from schema '%s'",
                deleted_in_schema,
                schema,
            )
        total_deleted += deleted_in_schema

    return total_deleted
