"""SQLite-backed store for symbols, edges and file records.

This is the default, zero-dependency store used by the CLI. It models the
repository graph with three tables (`files`, `symbols`, `edges`) plus an FTS5
virtual table over symbol names and qualified names so the retrieval layer can
do fast keyword + symbol search without a separate search engine.

The schema is intentionally close to the Pydantic models in
:mod:`aviator_core.models` to keep the persistence layer thin.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional

from aviator_core.models import Edge, FileRecord, Symbol


_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path           TEXT PRIMARY KEY,
    language       TEXT NOT NULL,
    package        TEXT,
    sha256         TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    parse_ok       INTEGER NOT NULL,
    parse_error    TEXT,
    indexed_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS symbols (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    package        TEXT,
    parent_id      TEXT,
    path           TEXT NOT NULL,
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    start_col      INTEGER NOT NULL,
    end_col        INTEGER NOT NULL,
    signature      TEXT,
    return_type    TEXT,
    modifiers      TEXT,        -- JSON array
    annotations    TEXT,        -- JSON array
    parameter_types TEXT,       -- JSON array
    spring_stereotype TEXT,     -- Spring stereotype (Controller, Service, etc.)
    spring_endpoints TEXT,      -- JSON array of REST endpoints
    spring_dependencies TEXT,   -- JSON array of @Autowired dependencies
    is_feign_client INTEGER DEFAULT 0,
    feign_service_name TEXT,
    FOREIGN KEY (path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbols_name           ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified_name ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind           ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_symbols_path           ON symbols(path);
CREATE INDEX IF NOT EXISTS idx_symbols_parent         ON symbols(parent_id);
CREATE INDEX IF NOT EXISTS idx_symbols_spring_stereotype ON symbols(spring_stereotype);
CREATE INDEX IF NOT EXISTS idx_symbols_is_feign_client ON symbols(is_feign_client);

CREATE TABLE IF NOT EXISTS edges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    src_id         TEXT NOT NULL,
    dst_id         TEXT,
    dst_name       TEXT NOT NULL,
    path           TEXT,
    start_line     INTEGER,
    FOREIGN KEY (src_id) REFERENCES symbols(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_edges_src      ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst      ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst_name ON edges(dst_name);
CREATE INDEX IF NOT EXISTS idx_edges_kind     ON edges(kind);

-- Full-text search over symbol identifiers.
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name,
    qualified_name,
    signature,
    content='symbols',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, qualified_name, signature)
    VALUES (new.rowid, new.name, new.qualified_name, COALESCE(new.signature, ''));
END;

CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, signature)
    VALUES ('delete', old.rowid, old.name, old.qualified_name, COALESCE(old.signature, ''));
END;
-- Project configuration: pom.xml / application.yml extracted metadata.
CREATE TABLE IF NOT EXISTS project_config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    source     TEXT,   -- which file it came from
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class SqliteStore:
    """A small wrapper that exposes idiomatic batch upserts."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def upsert_project_config(self, key: str, value: str, source: str) -> None:
        self._conn.execute(
            """
            INSERT INTO project_config(key, value, source, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                source=excluded.source, updated_at=excluded.updated_at
            """,
            (key, value, source),
        )

    def get_project_config(self) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT key, value FROM project_config ORDER BY key"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    # ------------------------------------------------------------------ writes

    def upsert_file(self, file: FileRecord) -> None:
        # Replace cascades to symbols/edges via FK so a re-index of one file
        # produces a clean view without stale rows.
        self._conn.execute("DELETE FROM files WHERE path = ?", (file.path,))
        self._conn.execute(
            """
            INSERT INTO files(path, language, package, sha256, size_bytes,
                              parse_ok, parse_error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file.path,
                file.language,
                file.package,
                file.sha256,
                file.size_bytes,
                1 if file.parse_ok else 0,
                file.parse_error,
            ),
        )

    def insert_symbols(self, symbols: Iterable[Symbol]) -> None:
        rows = [
            (
                s.id,
                s.kind.value,
                s.name,
                s.qualified_name,
                s.package,
                s.parent_id,
                s.location.path,
                s.location.start_line,
                s.location.end_line,
                s.location.start_col,
                s.location.end_col,
                s.signature,
                s.return_type,
                json.dumps(s.modifiers),
                json.dumps(s.annotations),
                json.dumps(s.parameter_types),
                s.spring_stereotype,
                json.dumps(s.spring_endpoints),
                json.dumps(s.spring_dependencies),
                1 if s.is_feign_client else 0,
                s.feign_service_name,
            )
            for s in symbols
        ]
        if not rows:
            return
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO symbols(
                id, kind, name, qualified_name, package, parent_id,
                path, start_line, end_line, start_col, end_col,
                signature, return_type, modifiers, annotations, parameter_types,
                spring_stereotype, spring_endpoints, spring_dependencies,
                is_feign_client, feign_service_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def insert_edges(self, edges: Iterable[Edge]) -> None:
        rows = [
            (
                e.kind.value,
                e.src_id,
                e.dst_id,
                e.dst_name,
                e.location.path if e.location else None,
                e.location.start_line if e.location else None,
            )
            for e in edges
        ]
        if not rows:
            return
        self._conn.executemany(
            """
            INSERT INTO edges(kind, src_id, dst_id, dst_name, path, start_line)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------- reads

    def file_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def symbol_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

    def edge_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    def search_symbols(
        self,
        text: str,
        *,
        kinds: Optional[list[str]] = None,
        limit: int = 25,
    ) -> list[sqlite3.Row]:
        """Hybrid keyword + FTS search over symbols.

        Strategy:
        1. Try FTS5 MATCH first (token-aware, fast).
        2. Fall back to LIKE if MATCH yields nothing (handles partial / mixed-case).
        """
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        kind_filter = ""
        params: list[object] = []
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            kind_filter = f" AND s.kind IN ({placeholders})"
            params.extend(kinds)

        # FTS attempt.
        try:
            fts_params = [text] + params + [limit]
            rows = cur.execute(
                f"""
                SELECT s.* FROM symbols_fts f
                JOIN symbols s ON s.rowid = f.rowid
                WHERE symbols_fts MATCH ?{kind_filter}
                LIMIT ?
                """,
                fts_params,
            ).fetchall()
            if rows:
                return rows
        except sqlite3.OperationalError:
            # Malformed MATCH expression: fall through to LIKE.
            pass

        like_params = [f"%{text}%", f"%{text}%"] + params + [limit]
        return cur.execute(
            f"""
            SELECT s.* FROM symbols s
            WHERE (s.name LIKE ? OR s.qualified_name LIKE ?){kind_filter}
            LIMIT ?
            """,
            like_params,
        ).fetchall()

    def neighbors(self, symbol_id: str, *, direction: str = "out") -> list[sqlite3.Row]:
        """Return adjacent edges for a symbol.

        direction: 'out' (edges where symbol is src), 'in' (where it is dst), or 'both'.
        """
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        if direction == "out":
            return cur.execute(
                "SELECT * FROM edges WHERE src_id = ?", (symbol_id,)
            ).fetchall()
        if direction == "in":
            return cur.execute(
                "SELECT * FROM edges WHERE dst_id = ?", (symbol_id,)
            ).fetchall()
        return cur.execute(
            "SELECT * FROM edges WHERE src_id = ? OR dst_id = ?",
            (symbol_id, symbol_id),
        ).fetchall()

    def get_symbol(self, symbol_id: str) -> Optional[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        return self._conn.execute(
            "SELECT * FROM symbols WHERE id = ?", (symbol_id,)
        ).fetchone()

    def close(self) -> None:
        self._conn.close()
