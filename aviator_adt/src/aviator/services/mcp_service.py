"""MCP service layer — business logic for MCP client and server management.

Sits between the REST routers and the database repositories.  Uses
``DatabaseManager.session(schema_name)`` to obtain a scoped session and
passes it into the relevant repository — no raw session management in the
service layer.
"""

import json
import logging
import uuid
from typing import Any

from fastapi import HTTPException, status

from aviator.database import database_manager
from aviator.database.mcp_repository import MCPServerRepository
from aviator.database.models import MCPServerConfiguration
from aviator.database.tenant_lookup_repository import TenantLookupRepository
from aviator.exceptions import MCPToolAlreadyRegisteredError
from aviator.mcp import mcp_client_manager
from aviator.mcp.models import MCPServerConfig, MCPServerConfigResponse
from aviator.mcp.server.utils.utils import discover_tools
from aviator.services.tenant import tenant_id_to_schema_name
from aviator.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _schema_for(user: dict | None) -> str:
    """Derive the tenant schema name from the authenticated user dict."""
    tenant_id = user.get("tenantId") if user else None
    return tenant_id_to_schema_name(tenant_id)


def _require_consistent_tenant(user: dict | None) -> tuple[str | None, str | None]:
    """Return (tenant_id, subscription_id), raising 400 if only one is present."""
    tenant_id = user.get("tenantId") if user else None
    subscription_id = user.get("subscriptions")[0] if user and user.get("subscriptions") else None
    if bool(tenant_id) != bool(subscription_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token must include both tenantId and subscriptions, or neither",
        )
    return tenant_id, subscription_id


def _row_to_response(row: MCPServerConfiguration) -> MCPServerConfigResponse:
    """Convert an ORM server row to a response model."""
    config_dict = json.loads(row.server_config)
    config_dict["name"] = row.server_name
    config_dict["active"] = row.active
    config_dict["id"] = row.server_id
    return MCPServerConfigResponse(**config_dict)


# ---------------------------------------------------------------------------
# MCP client - server config management
# ---------------------------------------------------------------------------


class MCPClientService:
    """Business logic for managing remote MCP server configurations."""

    def create_server_config(self, payload: MCPServerConfig, user: dict | None) -> MCPServerConfigResponse:
        """Create a new MCP server configuration for the calling tenant."""
        schema_name = _schema_for(user)
        server_id = str(uuid.uuid4())
        server_config_json = json.dumps(payload.model_dump(exclude={"name", "active"}))
        logger.info(
            "Creating MCP server config tenant=%s schema=%s", user.get("tenantId") if user else "default", schema_name
        )
        try:
            with database_manager.session(schema_name) as db:
                MCPServerRepository(db).create(payload.name, server_id, server_config_json, payload.active)
            mcp_client_manager.refresh_tools(user)
            return MCPServerConfigResponse(id=server_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    def list_server_configs(self, user: dict | None) -> list[MCPServerConfigResponse]:
        """Return all MCP server configurations for the calling tenant."""
        with database_manager.session(_schema_for(user)) as db:
            return [_row_to_response(row) for row in MCPServerRepository(db).get_all()]

    def get_server_config(self, server_id: str, user: dict | None) -> MCPServerConfigResponse:
        """Return one MCP server configuration by ID."""
        with database_manager.session(_schema_for(user)) as db:
            row = MCPServerRepository(db).get(server_id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
        return _row_to_response(row)

    def update_server_config(
        self, server_id: str, payload: MCPServerConfig, user: dict | None
    ) -> MCPServerConfigResponse:
        """Update an existing MCP server configuration."""
        server_config_json = json.dumps(payload.model_dump(exclude={"name", "active"}))
        try:
            with database_manager.session(_schema_for(user)) as db:
                repo = MCPServerRepository(db)
                if not repo.get(server_id):
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
                repo.update(server_id, server_config_json, payload.active, payload.name)
            mcp_client_manager.refresh_tools(user)
            return MCPServerConfigResponse(id=server_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    def delete_server_config(self, server_id: str, user: dict | None) -> MCPServerConfigResponse:
        """Delete an MCP server configuration by ID."""
        with database_manager.session(_schema_for(user)) as db:
            row = MCPServerRepository(db).delete(server_id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
        mcp_client_manager.refresh_tools(user)
        return _row_to_response(row)


# ---------------------------------------------------------------------------
# MCP server lookup config / tool registration
# ---------------------------------------------------------------------------


class MCPServerService:
    """Business logic for binding MCP tools to tenants via ``tenant_lookup_config``."""

    def get_all_discovered_tools(self) -> list[dict[str, Any]]:
        """Return every tool exposed by the MCP server (name + description)."""
        return [
            {"name": name, "description": getattr(tool, "description", None)} for name, tool in discover_tools().items()
        ]

    def register_tools(self, tool_names: list[str], user: dict | None) -> dict[str, Any]:
        """Register allowed tools for the calling tenant/subscription.

        The database row stores the full merged tool set for the subscription,
        but the response payload is limited to the tools from the current
        request.
        """
        tenant_id, subscription_id = _require_consistent_tenant(user)
        schema_name = tenant_id_to_schema_name(tenant_id)

        available = {name: getattr(tool, "description", None) for name, tool in discover_tools().items()}
        allowed_payload = [{"name": n, "description": available[n]} for n in tool_names if n in available]
        if not allowed_payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="None of the requested tools are available"
            )

        invalid_tools = [name for name in tool_names if name not in available]
        if invalid_tools:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid tool(s) requested: {', '.join(invalid_tools)}",
            )

        try:
            with database_manager.session(schema_name) as db:
                result = TenantLookupRepository(db).create_or_update(allowed_payload, subscription_id)
                return {
                    "id": result.id,
                    "key": result.key,
                    # Return the tools as a list for API responses (router will
                    # serialize/validate via Pydantic). The DB still stores
                    # the JSON string; we only return the parsed list here.
                    "value": allowed_payload,
                    "subscription_id": result.subscription_id,
                }
        except MCPToolAlreadyRegisteredError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to register tool config")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to register tool config"
            ) from exc

    def list_registered_tools(self, user: dict | None) -> list[dict]:
        """Return the tools registered for the calling tenant/subscription."""
        tenant_id, subscription_id = _require_consistent_tenant(user)
        schema_name = tenant_id_to_schema_name(tenant_id)
        logger.info(
            "Listing registered tools for tenant_id='%s', subscription_id='%s', schema_name='%s'",
            tenant_id,
            subscription_id,
            schema_name,
        )
        try:
            with database_manager.session(schema_name) as db:
                rows = TenantLookupRepository(db).get(
                    subscription_id=subscription_id, key=settings.mcp_tools_config_key
                )
                if not rows:
                    return []
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to fetch tools")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch tools"
            ) from exc
        return [
            {
                "id": row.id,
                "key": row.key,
                "value": json.loads(row.value) if isinstance(row.value, str) else row.value,
                "subscription_id": row.subscription_id,
            }
            for row in rows
        ]

    def delete_tools(self, tool_names: list[str], user: dict | None) -> dict[str, Any]:
        """Remove specific tools from the registered list."""
        tenant_id, subscription_id = _require_consistent_tenant(user)
        schema_name = tenant_id_to_schema_name(tenant_id)

        try:
            with database_manager.session(schema_name) as db:
                repo = TenantLookupRepository(db)
                rows = repo.get(subscription_id=subscription_id, key=settings.mcp_tools_config_key)
                if not rows:
                    return {
                        "message": "No registered tools to remove.",
                    }

                registered = {tool.get("name") for row in rows for tool in json.loads(row.value)}
                not_registered = [t for t in tool_names if t not in registered]
                if not_registered:
                    raise HTTPException(  # noqa: TRY301
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Tool(s) not registered: {', '.join(not_registered)}",
                    )

                updated = repo.remove_tools(subscription_id, tool_names)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Failed to remove tools")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to remove tools"
            ) from exc

        if updated is None:
            return {
                "message": "Tools removed. Config entry deleted as no tools remain.",
                "removed_tools": tool_names,
                "subscription_id": subscription_id,
            }
        return {
            "message": "Tools removed successfully.",
            "removed_tools": tool_names,
            "subscription_id": subscription_id,
            "remaining_tools": json.loads(updated.value),
        }


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

mcp_client_service = MCPClientService()
mcp_server_service = MCPServerService()
