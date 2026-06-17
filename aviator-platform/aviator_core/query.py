"""High-level read queries built on top of :class:`SqliteStore`.

These helpers are the building blocks of the (future) hybrid retrieval layer:
keyword + symbol search now, semantic + graph traversal next.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from aviator_core.storage import SqliteStore


@dataclass
class SymbolHit:
    id: str
    kind: str
    name: str
    qualified_name: str
    path: str
    start_line: int
    signature: Optional[str]


@dataclass
class EdgeHit:
    kind: str
    src_id: str
    dst_id: Optional[str]
    dst_name: str
    path: Optional[str]
    start_line: Optional[int]


def _row_to_symbol(row: sqlite3.Row) -> SymbolHit:
    return SymbolHit(
        id=row["id"],
        kind=row["kind"],
        name=row["name"],
        qualified_name=row["qualified_name"],
        path=row["path"],
        start_line=row["start_line"],
        signature=row["signature"],
    )


def _row_to_edge(row: sqlite3.Row) -> EdgeHit:
    return EdgeHit(
        kind=row["kind"],
        src_id=row["src_id"],
        dst_id=row["dst_id"],
        dst_name=row["dst_name"],
        path=row["path"],
        start_line=row["start_line"],
    )


def find_symbols(
    store: SqliteStore,
    text: str,
    *,
    kinds: Optional[list[str]] = None,
    limit: int = 25,
) -> list[SymbolHit]:
    return [_row_to_symbol(r) for r in store.search_symbols(text, kinds=kinds, limit=limit)]


def neighbors(store: SqliteStore, symbol_id: str, *, direction: str = "out") -> list[EdgeHit]:
    return [_row_to_edge(r) for r in store.neighbors(symbol_id, direction=direction)]


def callers(store: SqliteStore, symbol_name: str, limit: int = 25) -> list[EdgeHit]:
    """Return CALLS edges whose target name matches `symbol_name`.

    The edge `dst_name` is best-effort textual (e.g. `service.doIt` or `doIt`)
    so we accept partial matches via `LIKE`.
    """
    store._conn.row_factory = sqlite3.Row  # noqa: SLF001 - internal helper
    rows = store._conn.execute(  # noqa: SLF001
        """
        SELECT * FROM edges
        WHERE kind = 'calls' AND (dst_name = ? OR dst_name LIKE ?)
        LIMIT ?
        """,
        (symbol_name, f"%{symbol_name}%", limit),
    ).fetchall()
    return [_row_to_edge(r) for r in rows]
