"""Operations for the ``tenant_lookup_config`` table (TenantLookupConfig)."""

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from aviator.exceptions import MCPToolAlreadyRegisteredError

from .models import TenantLookupConfig

logger = logging.getLogger(__name__)


class TenantLookupRepository:
    """Repository for ``TenantLookupConfig`` rows.

    Receives a ``Session`` scoped to the correct tenant schema from
    ``DatabaseManager.session(schema_name)``.  All session lifecycle is
    handled by the caller via the context manager - no open/close here.

    Usage::

        with database_manager.session(schema_name) as db:
            repo = TenantLookupRepository(db)
            repo.create_or_update(tools, subscription_id)
    """

    def __init__(self, db: Session) -> None:
        """Initialize TenantLookupRepository with a scoped database session."""
        self._db = db

    def create_or_update(self, tools: list[dict], subscription_id: str | None) -> TenantLookupConfig:
        """Upsert ALLOWED_MCP_TOOLS for a subscription.

        If a row with the same ``subscription_id`` already exists, merges new
        tools into the existing list only when none of the requested tool names
        are already present.

        Args:
            tools: Allowed tools payload including names and descriptions.
            subscription_id: Subscription this config entry belongs to (can be None).

        Returns:
            Upserted TenantLookupConfig instance.

        Raises:
            ValueError: If ``tools`` list is empty.

        """
        if not tools:
            msg = "tools list is required and cannot be empty"
            raise ValueError(msg)

        key = "ALLOWED_MCP_TOOLS"

        existing = self._db.scalars(
            select(TenantLookupConfig).where(TenantLookupConfig.subscription_id == subscription_id)
        ).first()

        if existing:
            existing.key = key
            current_tools: list[dict] = json.loads(existing.value)
            current_by_name = {t["name"]: t for t in current_tools}
            duplicate_names = [t["name"] for t in tools if t["name"] in current_by_name]
            if duplicate_names:
                duplicate_names_str = ", ".join(duplicate_names)
                msg = f"Tool(s) already registered: {duplicate_names_str}"
                raise MCPToolAlreadyRegisteredError(msg)

            for t in tools:
                current_by_name[t["name"]] = t

            existing.value = json.dumps(list(current_by_name.values()))
            self._db.commit()
            self._db.refresh(existing)
            return existing

        config = TenantLookupConfig(key=key, value=json.dumps(tools), subscription_id=subscription_id)
        self._db.add(config)
        self._db.commit()
        self._db.refresh(config)
        return config

    def get(self, subscription_id: str | None = None, key: str | None = None) -> list[TenantLookupConfig]:
        """Fetch lookup config rows by optional filters.

        Args:
            subscription_id: Subscription to filter by; ``None`` matches NULL rows.
            key: Optional key to filter by.

        Returns:
            List of TenantLookupConfig instances ordered by id.

        """
        stmt = (
            select(TenantLookupConfig)
            .where(
                TenantLookupConfig.subscription_id == subscription_id,
                TenantLookupConfig.key == key,
            )
            .order_by(TenantLookupConfig.id)
        )
        return list(self._db.scalars(stmt).all())

    def remove_tools(self, subscription_id: str | None, tools_to_remove: list[str]) -> TenantLookupConfig | None:
        """Remove specific tools from the ALLOWED_MCP_TOOLS list.

        Finds the row by ``subscription_id``, removes the named tools from the
        JSON array, then persists the change.  If no tools remain the row is
        deleted entirely.

        Args:
            subscription_id: Subscription to look up (None for public schema).
            tools_to_remove: List of tool names to remove.

        Returns:
            Updated TenantLookupConfig if tools remain, None if the row was deleted
            or was not found.

        """
        config = self._db.scalars(
            select(TenantLookupConfig).where(
                TenantLookupConfig.subscription_id == subscription_id,
                TenantLookupConfig.key == "ALLOWED_MCP_TOOLS",
            )
        ).first()

        if not config:
            return None

        current_tools: list[dict] = json.loads(config.value)
        updated_tools = [t for t in current_tools if t.get("name") not in tools_to_remove]

        if not updated_tools:
            self._db.delete(config)
            self._db.commit()
            return None

        config.value = json.dumps(updated_tools)
        self._db.commit()
        self._db.refresh(config)
        return config
