"""MCP Client Manager for handling multiple MCP server connections."""

import json
import logging
from typing import Any, NamedTuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from aviator.database import database_manager
from aviator.database.mcp_repository import MCPServerRepository
from aviator.mcp.models import MCPServerConfig

from .auth.auth_manager import MCPAuthManager

logger = logging.getLogger(__name__)


class TenantContext(NamedTuple):
    """Cached context per tenant."""

    server_configs: dict[str, MCPServerConfig]
    client: MultiServerMCPClient | None
    tools_cache: list[BaseTool] | None


class MCPClientManager:
    """Manages multiple MCP server connections with multi-tenant support.

    - Singleton service instance
    - Per-tenant context storage (each tenant has isolated configs/client/tools)
    - Tenant ID extracted from user context
    - Lazy initialization on first request
    """

    def __init__(self) -> None:
        """Initialize MCPClientManager with per-tenant context storage."""
        # Per-tenant isolated contexts - keyed by tenant_id
        self._tenant_contexts: dict[str, TenantContext] = {}

    def _get_current_tenant_id(self, user: dict | None = None) -> str:
        """Get current tenant ID from user context.

        Args:
            user: User context dict containing tenantId

        Returns:
            tenant_id from user context, or 'default' for non-tenant requests

        """
        if user and isinstance(user, dict):
            tenant_id = user.get("tenantId")
            if tenant_id:
                return str(tenant_id)

        # Fallback to 'default' for single-tenant or anonymous requests
        return "default"

    async def get_remote_mcp_tools(
        self, scope: str = "default", user: dict | None = None, request: object | None = None
    ) -> list[BaseTool]:
        """Get tools from remote MCP servers filtered by scope.

        Implements per-tenant caching with dynamic OTDS token injection:
        - Each tenant's MCP server configs are loaded from database on first request
        - OTDS tokens from the request headers are injected before tool discovery
        - Client connections and tool lists are cached per tenant
        - Cache persists for the lifetime of the application

        Args:
            scope: Tool scope filter ("default", "custom", "all")
            user: User context dict containing tenantId and auth info
            request: FastAPI Request object containing headers with potential OTDS token

        Returns:
            List of filtered BaseTool objects from MCP servers

        """
        # Get current tenant ID from user context
        tenant_id = self._get_current_tenant_id(user)

        # Get or create tenant-specific context (cached per tenant)
        if tenant_id not in self._tenant_contexts:
            logger.info("Initializing MCP context for tenant: %s", tenant_id)

            # Load tenant-specific configs from database
            server_configs = await self._load_from_database(user)

            if not server_configs:
                logger.debug("No MCP server configs found for tenant: %s", tenant_id)
                # Store empty context to avoid repeated DB queries
                self._tenant_contexts[tenant_id] = TenantContext(server_configs={}, client=None, tools_cache=[])
                return []

            # Extract OTDS token from request headers and inject into configs before client initialization
            otds_token = self._extract_otds_token(request, user)
            if otds_token:
                self._inject_token_into_configs(server_configs, otds_token)

            # Initialize client and discover tools for this tenant
            client = await self._initialize_client(server_configs)
            tools_cache = await self._discover_tools(client, server_configs)

            # Store tenant context (isolated from other tenants)
            self._tenant_contexts[tenant_id] = TenantContext(
                server_configs=server_configs, client=client, tools_cache=tools_cache
            )
            logger.info(
                "MCP context initialized for tenant %s: %d tools from %d servers",
                tenant_id,
                len(tools_cache),
                len(server_configs),
            )

        # Use cached context for this tenant
        ctx = self._tenant_contexts[tenant_id]
        tools_cache = ctx.tools_cache or []
        server_configs = ctx.server_configs

        # If scope is "all", return everything
        if scope == "all":
            return tools_cache

        # Filter tools based on server configuration
        filtered_tools = []

        for tool in tools_cache:
            # Find the server config for this tool
            server_config = self._find_server_for_tool(tool, server_configs)
            if not server_config:
                # Tool from unknown server - include if scope is default (backward compatibility)
                if scope == "default":
                    filtered_tools.append(tool)
                continue

            # Apply scope filtering
            if scope == "default":
                # Include if tool_scope is "default"
                if server_config.tool_scope == "default":
                    filtered_tools.append(tool)

            elif scope == "custom" and server_config.tool_scope == "custom":
                filtered_tools.append(tool)

        return filtered_tools

    def _find_server_for_tool(
        self, tool: BaseTool, server_configs: dict[str, MCPServerConfig]
    ) -> MCPServerConfig | None:
        """Find the server configuration that provided this tool.

        With tool_name_prefix=True, tool names are formatted as 'servername_toolname'
        """
        # Check if tool name starts with any server name followed by underscore
        for server_name, config in server_configs.items():
            if tool.name.startswith(f"{server_name}_"):
                return config

        return None

    async def get_plugin_mcp_tools(self, user: dict | None = None, request: object | None = None) -> list[BaseTool]:
        """Get MCP tools filtered for plugin scope with OTDS token injection.

        Automatically extracts OTDS token from the request headers and injects it into
        all OTDS_TOKEN auth strategies before tool discovery. This ensures
        plugin nodes have properly authenticated access to MCP tools.

        Args:
            user: User context dict containing tenantId and auth info
            request: FastAPI Request object containing headers with potential OTDS token

        Returns:
            List[BaseTool]: MCP tools with tool_scope="custom" for use in plugin nodes

        """
        return await self.get_remote_mcp_tools(scope="custom", user=user, request=request)

    def inject_otds_token(self, token: str, server_names: list[str] | None = None, user: dict | None = None) -> None:
        """Inject token into all OTDSTokenAuthStrategy instances for current tenant.

        Args:
            token: The token to inject (from incoming request)
            server_names: Optional list of server names to update. If None, updates all OTDS token servers.
            user: User context dict containing tenantId

        """
        tenant_id = self._get_current_tenant_id(user)

        if tenant_id not in self._tenant_contexts:
            logger.debug("No tenant context to inject token into: %s", tenant_id)
            return

        ctx = self._tenant_contexts[tenant_id]
        changed_servers = []

        for server_name, config in ctx.server_configs.items():
            # Skip if specific servers requested and this isn't one of them
            if server_names is not None and server_name not in server_names:
                continue

            # Check if this server uses OTDS token auth
            if hasattr(config, "auth_schema") and config.auth_schema and config.auth_schema.type == "OTDS_TOKEN":
                try:
                    current_token = getattr(config.auth_schema, "token", None)
                    if current_token == token:
                        logger.debug("OTDS token unchanged for server %s, skipping cache invalidation", server_name)
                        continue

                    # Token changed — update auth_schema directly so re-discovery picks it up
                    config.auth_schema.token = token
                    changed_servers.append(server_name)
                    logger.debug("Updated OTDS token for server: %s", server_name)

                except Exception as e:
                    logger.error("Failed to inject token for server %s: %s", server_name, e)

        # Only invalidate the cache when the token actually changed.
        # Discovered tools have the auth token baked into their HTTP headers,
        # so re-discovery is required when the token rotates.
        if changed_servers:
            logger.info(
                "Token changed for tenant %s on %d server(s) — invalidating tool cache: %s",
                tenant_id,
                len(changed_servers),
                ", ".join(changed_servers),
            )
            del self._tenant_contexts[tenant_id]
        else:
            logger.debug("No OTDS token auth servers found for token injection")

    async def get_assistant_tools(self, user: dict | None = None, request: object | None = None) -> list[BaseTool]:
        """Get tools for the assistant node with OTDS token injection.

        Automatically extracts OTDS token from the request headers and injects it into
        all OTDS_TOKEN auth strategies before tool discovery. This ensures
        the assistant has properly authenticated access to MCP tools.

        Args:
            user: User context dict containing tenantId and auth info
            request: FastAPI Request object containing headers with potential OTDS token

        """
        return await self.get_remote_mcp_tools(scope="default", user=user, request=request)

    async def refresh_tools(self, user: dict | None = None) -> None:
        """Invalidate the tools cache for the current tenant.

        Forces a full reload from the database on the next ``get_remote_mcp_tools``
        call — including re-reading server configs from DB, re-initialising the
        client, and re-discovering all tools.

        Args:
            user: User context dict containing tenantId

        """
        tenant_id = self._get_current_tenant_id(user)
        if tenant_id in self._tenant_contexts:
            del self._tenant_contexts[tenant_id]
            logger.info("MCP tool cache invalidated for tenant '%s' — will reload from DB on next request", tenant_id)
        else:
            logger.debug("No cached MCP context to invalidate for tenant: %s", tenant_id)

    async def get_server_status(self, user: dict | None = None) -> dict[str, dict[str, Any]]:
        """Get simple status of all MCP servers for current tenant.

        Args:
            user: User context dict containing tenantId

        """
        tenant_id = self._get_current_tenant_id(user)

        if tenant_id not in self._tenant_contexts:
            return {
                "_client_summary": {
                    "tenant_id": tenant_id,
                    "initialized": False,
                    "message": "No MCP context initialized for this tenant",
                }
            }

        ctx = self._tenant_contexts[tenant_id]
        total_tool_count = len(ctx.tools_cache or [])

        status = {}
        for name, config in ctx.server_configs.items():
            # Check if this server is connected via the client
            connected = ctx.client is not None and config.active

            status[name] = {
                "active": config.active,
                "connected": connected,
                "transport": config.transport,
                "url": config.url,
                "command": config.command,
            }

        # Add overall client status
        status["_client_summary"] = {
            "tenant_id": tenant_id,
            "total_servers_configured": len(ctx.server_configs),
            "total_servers_active": len([c for c in ctx.server_configs.values() if c.active]),
            "client_initialized": ctx.client is not None,
            "total_tools_available": total_tool_count,
        }

        return status

    # Private methods

    def _extract_otds_token(self, request: object | None = None, user: dict | None = None) -> str | None:
        """Extract OTDS token from Authorization header or user context.

        Tries request headers first, then falls back to user context dict
        (used when called from the LangGraph node where no Request object exists).

        Args:
            request: FastAPI Request object with headers
            user: User context dict that may contain an 'authorization' key

        Returns:
            Token without Bearer prefix, or None if not present

        """
        if request and hasattr(request, "headers"):
            auth_header = request.headers.get("authorization")
            if auth_header:
                if auth_header.lower().startswith("bearer "):
                    return auth_header[7:]
                return auth_header

        # Fallback: extract from user context dict (LangGraph graph node path)
        if user and isinstance(user, dict):
            auth_header = user.get("authorization")
            if auth_header:
                if auth_header.lower().startswith("bearer "):
                    return auth_header[7:]
                return auth_header

        return None

    def _inject_token_into_configs(self, server_configs: dict[str, MCPServerConfig], token: str) -> None:
        """Inject OTDS token into all server configs that use OTDS_TOKEN auth.

        This modifies the auth_schema objects in-place so the token is available
        when the client is initialized and connects to servers.

        Args:
            server_configs: Dict of server configs to update
            token: OTDS token to inject

        """
        injected_count = 0

        for server_name, config in server_configs.items():
            # Check if server uses OTDS_TOKEN auth
            if (
                hasattr(config, "auth_schema")
                and config.auth_schema
                and hasattr(config.auth_schema, "type")
                and config.auth_schema.type == "OTDS_TOKEN"
            ):
                try:
                    # Set the token on the auth schema
                    if hasattr(config.auth_schema, "token"):
                        config.auth_schema.token = token
                    # Also try setting it as a dict field
                    elif isinstance(config.auth_schema, dict):
                        config.auth_schema["token"] = token

                    injected_count += 1
                    logger.debug("Injected OTDS token into server config: %s", server_name)

                except Exception as e:
                    logger.warning("Failed to inject token into server '%s': %s", server_name, e)

        if injected_count > 0:
            logger.info("Injected OTDS token into %d server config(s)", injected_count)

    async def _load_from_database(self, user: dict | None = None) -> dict[str, MCPServerConfig]:
        """Load MCP server configurations from the database.

        Args:
            user: User context dict containing tenantId for schema resolution

        Returns:
            Dict mapping server name to MCPServerConfig

        """
        from aviator.services.tenant import tenant_id_to_schema_name

        server_configs = {}

        # Extract tenant_id and convert to schema_name (same pattern as rag.py)
        # If no tenantId provided, tenant_id_to_schema_name(None) returns public/default schema
        tenant_id = user.get("tenantId") if user else None
        schema_name = tenant_id_to_schema_name(tenant_id)

        logger.debug("Loading MCP configs for tenant_id=%s, schema=%s", tenant_id or "default", schema_name)

        try:
            with database_manager.session(schema_name) as db:
                servers = MCPServerRepository(db).get_all()

            if not servers:
                logger.info("No MCP servers found in database (schema: %s)", schema_name)
                return server_configs

            logger.info("Found %d MCP server configs in database (schema: %s)", len(servers), schema_name)

            for server in servers:
                try:
                    # Parse the JSON server_config field
                    server_config_dict = json.loads(server.server_config)

                    # Add name and active fields from DB model
                    server_config_dict["name"] = server.server_name
                    server_config_dict["active"] = server.active

                    # Directly create MCPServerConfig from the complete config
                    config = MCPServerConfig(**server_config_dict)

                    server_configs[config.name] = config
                    logger.info("Loaded MCP server from DB: %s (active=%s)", config.name, config.active)

                except Exception as e:
                    logger.error("Failed to load server config from DB for %s: %s", server.server_name, e)
                    continue

        except Exception as e:
            logger.error("Failed to load MCP configs from database: %s", e)

        return server_configs

    def _build_server_client_config(self, config: MCPServerConfig) -> dict[str, Any] | None:
        """Build the single-server config dict for MultiServerMCPClient.

        Returns None if the config is invalid.
        """
        try:
            server_config: dict[str, Any] = {"transport": config.transport, "headers": {}}

            if config.transport in {"streamable_http", "sse"} and config.url:
                server_url = config.url

                if hasattr(config, "auth_schema") and config.auth_schema:
                    try:
                        auth_strategy = MCPAuthManager.create_strategy(config.auth_schema)
                        auth_headers = auth_strategy.get_headers()
                        if auth_headers:
                            server_config["headers"].update(auth_headers)
                            logger.debug("Applied auth headers for '%s': %s", config.name, list(auth_headers.keys()))
                        auth_params = auth_strategy.get_query_params()
                        if auth_params:
                            server_url = self._add_query_params(server_url, auth_params)
                    except Exception as e:
                        logger.error("Failed to apply authentication for server '%s': %s", config.name, e)

                server_config["url"] = server_url

            elif config.transport == "stdio" and config.command:
                server_config["command"] = config.command
                server_config["args"] = config.args
            else:
                logger.error("Invalid MCP server configuration for %s", config.name)
                return None

        except Exception as e:
            logger.error("Failed to build config for server '%s': %s", config.name, e)
            return None
        else:
            return server_config

    async def _initialize_client(self, server_configs: dict[str, MCPServerConfig]) -> MultiServerMCPClient | None:
        """Build per-server client configs; actual connections happen in _discover_tools.

        Returns a sentinel MultiServerMCPClient (or None) kept for API compatibility.
        The real work is done per-server in _discover_tools to isolate failures.
        """
        active_configs = [config for config in server_configs.values() if config.active]

        if not active_configs:
            logger.info("No active MCP servers to initialize")
            return None

        logger.info("Initializing MCP client with %d servers...", len(active_configs))
        # Return a truthy sentinel so callers know servers exist; per-server
        # clients are created inside _discover_tools for fault isolation.
        return MultiServerMCPClient({}, tool_name_prefix=True)

    def _add_query_params(self, url: str, params: dict[str, str]) -> str:
        """Add query parameters to URL, preserving existing ones."""
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)

        # Add new auth parameters
        for key, value in params.items():
            query_params[key] = [value]

        # Rebuild URL with new parameters
        new_query = urlencode(query_params, doseq=True)
        new_url = urlunparse(
            (parsed_url.scheme, parsed_url.netloc, parsed_url.path, parsed_url.params, new_query, parsed_url.fragment)
        )

        return new_url

    async def _discover_tools(
        self, _client: MultiServerMCPClient | None, server_configs: dict[str, MCPServerConfig]
    ) -> list[BaseTool]:
        """Discover tools from MCP servers, connecting to each server independently.

        Each server is queried via its own MultiServerMCPClient so that a single
        server failure does not abort tool discovery for the remaining servers.

        Args:
            client: Unused — kept for API compatibility. Per-server clients are
                    created internally from server_configs.
            server_configs: Dict mapping server name to MCPServerConfig

        Returns:
            List of discovered BaseTool objects (partial list if some servers fail)

        """
        if not server_configs:
            logger.warning("No MCP server configs to discover tools from")
            return []

        logger.info("Discovering tools from MCP servers...")
        all_tools: list[BaseTool] = []
        active_configs = [c for c in server_configs.values() if c.active]

        for config in active_configs:
            server_cfg = self._build_server_client_config(config)
            if not server_cfg:
                continue
            try:
                per_server_client = MultiServerMCPClient({config.name: server_cfg}, tool_name_prefix=True)
                tools = await per_server_client.get_tools()
                logger.info("✓ [%s] discovered %d tools", config.name, len(tools))
                all_tools.extend(tools)
            except Exception as e:
                logger.error("✗ [%s] failed to connect: %s", config.name, e)
                # Unpack ExceptionGroup sub-exceptions for diagnosis
                if hasattr(e, "exceptions"):
                    for i, sub_exc in enumerate(e.exceptions, 1):
                        logger.error("  Sub-exception %d: [%s] repr=%r", i, type(sub_exc).__name__, sub_exc)
                        cause = getattr(sub_exc, "__cause__", None) or getattr(sub_exc, "__context__", None)
                        if cause:
                            logger.error("    Caused by: [%s] %r", type(cause).__name__, cause)
                            inner = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
                            if inner:
                                logger.error("      Root cause: [%s] %r", type(inner).__name__, inner)
                        if hasattr(sub_exc, "exceptions"):
                            for j, nested in enumerate(sub_exc.exceptions, 1):
                                logger.error("    Nested %d.%d: [%s] %r", i, j, type(nested).__name__, nested)
                # Continue to next server — don't let one failure abort the rest
                continue

        if all_tools:
            logger.info(
                "✓ Successfully discovered %d tools from %d/%d servers",
                len(all_tools),
                len(active_configs),
                len(server_configs),
            )
        else:
            logger.warning("⚠ No tools discovered from any MCP server")

        return all_tools
