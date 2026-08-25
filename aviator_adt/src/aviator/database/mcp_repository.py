"""MCP server configuration repository.

Provides operations against ``MCPServerConfiguration`` as a class that
receives an already-scoped ``Session`` from ``DatabaseManager.session()``.

No session lifecycle here - open/close is the caller''s responsibility
(typically handled by the ``database_manager.session()`` context manager).
"""

import json
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MCPServerConfiguration


class MCPServerRepository:
    """Repository for ``MCPServerConfiguration`` rows.

    Receives a ``Session`` scoped to the correct tenant schema from
    ``DatabaseManager.session(schema_name)``.  All methods operate on that
    session - no session management logic inside.

    Usage::

        with database_manager.session(schema_name) as db:
            repo = MCPServerRepository(db)
            server = repo.create(name, server_id, config_json, active)
    """

    def __init__(self, db: Session) -> None:
        """Initialize MCPServerRepository with a scoped database session."""
        self._db = db

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_host(server_config: str) -> str | None:
        """Return ``scheme://host`` from a config JSON string, or ``None``."""
        try:
            config = json.loads(server_config)
            url = config.get("url")
            if url:
                parsed = urlparse(url)
                return f"{parsed.scheme}://{parsed.netloc}"
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    def _check_duplicate_host(self, server_config: str, exclude_server_id: str | None = None) -> None:
        """Raise ``ValueError`` if another row already uses the same host URL."""
        host = self._extract_host(server_config)
        if not host:
            return
        stmt = select(MCPServerConfiguration)
        if exclude_server_id:
            stmt = stmt.where(MCPServerConfiguration.server_id != exclude_server_id)
        for row in self._db.scalars(stmt).all():
            if self._extract_host(row.server_config) == host:
                msg = f"A server with host URL '{host}' already exists: {row.server_name}"
                raise ValueError(msg)

    def _check_duplicate_name(self, server_name: str, exclude_server_id: str | None = None) -> None:
        """Raise ``ValueError`` if another row already uses the same server name."""
        stmt = select(MCPServerConfiguration).where(MCPServerConfiguration.server_name == server_name)
        if exclude_server_id:
            stmt = stmt.where(MCPServerConfiguration.server_id != exclude_server_id)
        existing = self._db.scalars(stmt).first()
        if existing:
            msg = f"A server with name '{server_name}' already exists"
            raise ValueError(msg)

    def create(
        self, server_name: str, server_id: str, server_config: str, active: bool = True
    ) -> MCPServerConfiguration:
        """Insert a new MCP server configuration row."""
        self._check_duplicate_name(server_name)
        self._check_duplicate_host(server_config)
        server = MCPServerConfiguration(
            server_name=server_name,
            server_id=server_id,
            server_config=server_config,
            active=active,
        )
        self._db.add(server)
        self._db.commit()
        self._db.refresh(server)
        return server

    def get(self, server_id: str) -> MCPServerConfiguration | None:
        """Return the row for *server_id*, or ``None``."""
        return self._db.scalars(
            select(MCPServerConfiguration).where(MCPServerConfiguration.server_id == server_id)
        ).first()

    def get_all(self) -> list[MCPServerConfiguration]:
        """Return all server configuration rows."""
        return list(self._db.scalars(select(MCPServerConfiguration)).all())

    def update(
        self,
        server_id: str,
        server_config: str,
        active: bool | None = None,
        server_name: str | None = None,
    ) -> MCPServerConfiguration | None:
        """Update an existing row. Returns the updated row or ``None`` if not found."""
        server = self._db.scalars(
            select(MCPServerConfiguration).where(MCPServerConfiguration.server_id == server_id)
        ).first()
        if server:
            self._check_duplicate_name(server_name or server.server_name, exclude_server_id=server_id)
            self._check_duplicate_host(server_config, exclude_server_id=server_id)
            server.server_config = server_config
            if active is not None:
                server.active = active
            if server_name is not None:
                server.server_name = server_name
            self._db.commit()
            self._db.refresh(server)
        return server

    def delete(self, server_id: str) -> MCPServerConfiguration | None:
        """Delete the row for *server_id*. Returns the deleted row or ``None``."""
        server = self._db.scalars(
            select(MCPServerConfiguration).where(MCPServerConfiguration.server_id == server_id)
        ).first()
        if server:
            self._db.delete(server)
            self._db.commit()
        return server
