"""Post-migration verification — find and repair missing rows.

Compares source row count against migrated rows (tagged with
``_source = 'csai_legacy'``) across all target schemas.  The ``report``
command does a lightweight count comparison; ``repair`` performs a full
ID-level diff and directly inserts missing rows into the target database.

Usage:
    # Report missing row count (no changes):
    uv run python -m migration.verify report

    # Directly insert missing rows into target database:
    uv run python -m migration.verify repair
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, MetaData, Table, Text, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from migration.consumer import _insert_batch, _parse_embedding
from migration.db import get_engine
from migration.models import SourceRow
from migration.schema_manager import resolve_target_schema
from migration.settings import settings

logger = logging.getLogger(__name__)

# Number of source IDs to compare per query batch.
_VERIFY_BATCH_SIZE = 10_000

_CSAI_LEGACY = "csai_legacy"


def _get_source_table() -> Table:
    metadata = MetaData(schema=settings.source_schema)
    return Table(
        settings.source_table,
        metadata,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("text", Text),
        Column("metadata", JSONB),
        Column("embedding", Vector()),
        autoload_with=None,
        extend_existing=True,
    )


def _get_target_table(schema: str) -> Table:
    """Return a SQLAlchemy :class:`Table` for the target vector store."""
    metadata = MetaData(schema=schema)
    return Table(
        settings.target_table,
        metadata,
        Column("langchain_id", UUID(as_uuid=True), primary_key=True),
        Column("content", Text),
        Column("embedding", Vector()),
        Column("langchain_metadata", JSONB),
        Column("workspace_id", Text),
        Column("document_id", Text),
        Column("text_hash", Text),
        extend_existing=True,
    )


def _get_all_target_schemas() -> list[str]:
    """Return schemas that contain the target table."""
    engine = get_engine(settings.target_dsn)
    with Session(engine) as session:
        result = session.execute(
            text("SELECT DISTINCT table_schema FROM information_schema.tables WHERE table_name = :table"),
            {"table": settings.target_table},
        )
        return [row[0] for row in result]


def _count_migrated_rows(schemas: list[str]) -> dict[str, int]:
    """Count rows with ``_source = 'csai_legacy'`` per target schema.

    Returns a dict mapping schema name → migrated row count.
    """
    engine = get_engine(settings.target_dsn)
    counts: dict[str, int] = {}
    for schema in schemas:
        tbl = _get_target_table(schema)
        stmt = select(func.count()).select_from(tbl).where(tbl.c.langchain_metadata["_source"].astext == _CSAI_LEGACY)
        with Session(engine) as session:
            count = session.execute(stmt).scalar_one()
            counts[schema] = count
            logger.info("Target schema '%s': %d migrated rows", schema, count)
    return counts


def _collect_target_ids(schemas: list[str]) -> set[str]:
    """Collect all ``langchain_id`` values of migrated rows across target schemas.

    Only considers rows tagged with ``_source = 'csai_legacy'``.
    Returns a set of stringified UUIDs present in the target database.
    """
    engine = get_engine(settings.target_dsn)
    all_ids: set[str] = set()
    for schema in schemas:
        tbl = _get_target_table(schema)
        stmt = select(tbl.c.langchain_id).where(tbl.c.langchain_metadata["_source"].astext == _CSAI_LEGACY)
        with Session(engine) as session:
            result = session.execute(stmt)
            for (row_id,) in result:
                all_ids.add(str(row_id))
    logger.info("Total migrated rows across %d schemas: %d", len(schemas), len(all_ids))
    return all_ids


def _find_missing_ids() -> list[str]:
    """Return source row IDs that are missing from all target schemas."""
    target_schemas = _get_all_target_schemas()
    if not target_schemas:
        logger.warning("No target schemas found — has migration run?")
        return []

    target_ids = _collect_target_ids(target_schemas)

    source_engine = get_engine(settings.source_dsn)
    tbl = _get_source_table()
    source_count_stmt = select(func.count()).select_from(tbl)
    with Session(source_engine) as session:
        source_total = session.execute(source_count_stmt).scalar_one()
    logger.info("Source table has %d rows", source_total)

    missing: list[str] = []
    last_id: str | None = None
    checked = 0

    while checked < source_total:
        stmt = select(tbl.c.id).order_by(tbl.c.id).limit(_VERIFY_BATCH_SIZE)
        if last_id:
            stmt = stmt.where(tbl.c.id > last_id)
        with Session(source_engine) as session:
            rows = session.execute(stmt).fetchall()
        if not rows:
            break
        for (row_id,) in rows:
            sid = str(row_id)
            if sid not in target_ids:
                missing.append(sid)
        last_id = str(rows[-1][0])
        checked += len(rows)
        if checked % 100_000 == 0:
            logger.info("Verified %d / %d  (missing so far: %d)", checked, source_total, len(missing))

    logger.info(
        "Verification complete: %d source rows, %d in target, %d missing",
        source_total,
        len(target_ids),
        len(missing),
    )
    return missing


def report() -> int:
    """Report the number of missing rows using a fast count comparison.

    Counts source rows and compares against the total number of rows
    tagged with ``_source = 'csai_legacy'`` across all target schemas.

    Returns the number of missing rows (0 means migration is complete).
    """
    target_schemas = _get_all_target_schemas()
    if not target_schemas:
        logger.warning("No target schemas found — has migration run?")
        return -1

    source_engine = get_engine(settings.source_dsn)
    tbl = _get_source_table()
    with Session(source_engine) as session:
        source_total = session.execute(select(func.count()).select_from(tbl)).scalar_one()
    logger.info("Source table: %d rows", source_total)

    schema_counts = _count_migrated_rows(target_schemas)
    target_total = sum(schema_counts.values())
    logger.info("Target total (csai_legacy): %d rows", target_total)

    missing = source_total - target_total
    if missing == 0:
        logger.info("Counts match — migration is complete.")
    elif missing > 0:
        logger.warning("%d rows are missing from the target.", missing)
    else:
        logger.info(
            "Target has %d more rows than source (possible duplicates or concurrent inserts).",
            abs(missing),
        )
        missing = 0

    return missing


def repair() -> int:
    """Find missing rows and directly insert them into target schemas.

    Returns the number of rows repaired.
    """
    missing_ids = _find_missing_ids()
    if not missing_ids:
        logger.info("No missing rows — nothing to repair.")
        return 0

    logger.info("Repairing %d missing rows…", len(missing_ids))

    source_engine = get_engine(settings.source_dsn)
    src_table = _get_source_table()

    total_repaired = 0

    for i in range(0, len(missing_ids), settings.batch_size):
        batch_ids = missing_ids[i : i + settings.batch_size]

        with Session(source_engine) as session:
            stmt = select(
                src_table.c.id,
                src_table.c.text,
                src_table.c.metadata,
                src_table.c.embedding,
            ).where(src_table.c.id.in_(batch_ids))
            rows = session.execute(stmt).fetchall()

        grouped: dict[str, list[SourceRow]] = defaultdict(list)
        for row_id, row_text, metadata, embedding in rows:
            meta = metadata if isinstance(metadata, dict) else json.loads(metadata)
            schema = resolve_target_schema(meta)
            grouped[schema].append(
                SourceRow(
                    id=str(row_id),
                    text=row_text,
                    metadata=meta,
                    embedding=_parse_embedding(embedding),
                )
            )

        for schema, schema_rows in grouped.items():
            if settings.dry_run:
                logger.info("[DRY-RUN] Would repair %d rows → schema '%s'", len(schema_rows), schema)
            else:
                inserted = _insert_batch(schema, schema_rows)
                logger.debug("Inserted %d rows into schema '%s'", inserted, schema)
            total_repaired += len(schema_rows)

        logger.info("Repaired %d / %d missing rows", total_repaired, len(missing_ids))

    logger.info("Repair complete: %d rows repaired.", total_repaired)
    return total_repaired


def main() -> None:
    """CLI entrypoint: report | repair."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stdout,
    )

    if len(sys.argv) < 2 or sys.argv[1] not in ("report", "repair"):
        print("Usage: python -m migration.verify <report|repair>")  # noqa: T201
        sys.exit(1)

    action = sys.argv[1]
    if action == "report":
        missing = report()
        if missing > 0:
            sys.exit(1)
    elif action == "repair":
        repair()


if __name__ == "__main__":
    main()
