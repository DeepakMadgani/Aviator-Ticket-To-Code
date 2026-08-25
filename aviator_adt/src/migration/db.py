"""SQLAlchemy engine management for the migration service.

Provides a module-level helper to obtain a sync SQLAlchemy engine
connected to either the source or target database.  Engines are cached
per DSN so repeated calls reuse the same connection pool.
"""

from __future__ import annotations

import threading

from sqlalchemy import Engine, create_engine

# ── Constants ─────────────────────────────────────────────────────────

_POSTGRES_PREFIX = "postgresql://"
_PSYCOPG_PREFIX = "postgresql+psycopg://"

# ── Engine cache ──────────────────────────────────────────────────────

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def _normalise_url(dsn: str) -> str:
    """Ensure the DSN uses the ``psycopg`` (v3) SQLAlchemy driver prefix."""
    if dsn.startswith(_POSTGRES_PREFIX):
        return dsn.replace(_POSTGRES_PREFIX, _PSYCOPG_PREFIX, 1)
    return dsn


def get_engine(dsn: str, **kwargs) -> Engine:  # noqa: ANN003
    """Return a cached :class:`~sqlalchemy.engine.Engine` for *dsn*.

    Extra *kwargs* are forwarded to :func:`sqlalchemy.create_engine` only
    on the first call for a given DSN.
    """
    key = dsn
    if key in _engines:
        return _engines[key]

    with _lock:
        if key not in _engines:
            url = _normalise_url(dsn)
            _engines[key] = create_engine(url, **kwargs)
    return _engines[key]


def dispose_engine(dsn: str) -> None:
    """Dispose and remove a single cached engine for *dsn*.

    Used to force fresh connections after transient DB errors.
    """
    with _lock:
        engine = _engines.pop(dsn, None)
        if engine:
            engine.dispose()


def dispose_all() -> None:
    """Dispose of all cached engines (useful for tests / shutdown)."""
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
