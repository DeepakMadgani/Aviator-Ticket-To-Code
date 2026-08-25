"""Checkpoint / resume logic using a migration_state table in the target DB.

All public functions accept an optional ``session`` parameter.  When
provided the caller's session is reused, avoiding repeated
connect / disconnect cycles that can exhaust the server's
``max_connections`` limit during long migration runs.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from migration.db import get_engine
from migration.models import MigrationProgress
from migration.orm import Base, MigrationState
from migration.settings import settings

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

_MIGRATION_ID = "csai_to_adt"


@contextmanager
def _use_session(session: Session | None) -> Iterator[Session]:
    """Yield *session* if given, otherwise open (and close) a fresh session."""
    if session is not None:
        yield session
    else:
        engine = get_engine(settings.target_dsn)
        with Session(engine) as new_session:
            yield new_session


def ensure_state_table(session: Session | None = None) -> None:  # noqa: ARG001
    """Create the ``migration_state`` table if it does not exist."""
    engine = get_engine(settings.target_dsn)
    Base.metadata.create_all(engine, tables=[MigrationState.__table__], checkfirst=True)
    logger.info("migration_state table ensured.")


def load_progress(
    session: Session | None = None,
    *,
    migration_id: str = _MIGRATION_ID,
) -> MigrationProgress:
    """Load the current migration progress (with ``FOR UPDATE`` to prevent concurrent producers)."""
    ensure_state_table(session)
    with _use_session(session) as s:
        stmt = select(MigrationState).where(MigrationState.id == migration_id).with_for_update()
        row = s.execute(stmt).scalar_one_or_none()
        if row is None:
            return MigrationProgress()
        return MigrationProgress(
            last_id=str(row.last_id) if row.last_id else None,
            status=row.status,
            total_rows=row.total_rows,
            migrated=row.migrated or 0,
        )


def save_progress(
    progress: MigrationProgress,
    session: Session | None = None,
    *,
    migration_id: str = _MIGRATION_ID,
) -> None:
    """Persist the current migration progress."""
    now = datetime.now(tz=UTC)
    started = now if progress.migrated == 0 else None

    with _use_session(session) as s:
        stmt = pg_insert(MigrationState).values(
            id=migration_id,
            last_id=progress.last_id,
            status=progress.status,
            total_rows=progress.total_rows,
            migrated=progress.migrated,
            started_at=started,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MigrationState.id],
            set_={
                "last_id": stmt.excluded.last_id,
                "status": stmt.excluded.status,
                "total_rows": stmt.excluded.total_rows,
                "migrated": stmt.excluded.migrated,
                "updated_at": now,
            },
        )
        s.execute(stmt)
        s.commit()
    logger.debug("Checkpoint saved: last_id=%s  migrated=%s", progress.last_id, progress.migrated)


def mark_completed(
    total: int,
    session: Session | None = None,
    *,
    migration_id: str = _MIGRATION_ID,
) -> None:
    """Mark the migration as completed."""
    save_progress(
        MigrationProgress(
            status="completed",
            total_rows=total,
            migrated=total,
        ),
        session=session,
        migration_id=migration_id,
    )
    logger.info("Migration marked as completed (%d rows).", total)
