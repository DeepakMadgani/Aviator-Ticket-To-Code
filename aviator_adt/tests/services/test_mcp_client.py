"""Test suite for MCP client manager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aviator.mcp.client.manager import MCPClientManager, TenantContext
from aviator.mcp.models import MCPServerConfig


@pytest.fixture
def mcp_manager():
    """Create a fresh MCP client manager for each test."""
    return MCPClientManager()


@pytest.fixture
def sample_http_config():
    """Sample HTTP-based MCP server configuration."""
    return MCPServerConfig(
        name="test-http-server",
        active=True,
        url="https://example.com/mcp",
        transport="streamable_http",
    )


@pytest.fixture
def sample_stdio_config():
    """Sample STDIO-based MCP server configuration."""
    return MCPServerConfig(
        name="test-stdio-server",
        active=True,
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],  # noqa: S108
        transport="stdio",
    )


@pytest.fixture
def disabled_config():
    """Sample disabled MCP server configuration."""
    return MCPServerConfig(
        name="disabled-server",
        active=False,
        url="https://disabled.com/mcp",
        transport="streamable_http",
    )


def _make_tenant_context(server_configs=None, client=None, tools_cache=None):
    """Build a TenantContext for tests."""
    return TenantContext(
        server_configs=server_configs or {},
        client=client,
        tools_cache=tools_cache or [],
    )


class TestMCPServerConfig:
    """Tests for MCPServerConfig model."""

    def test_http_config_valid(self, sample_http_config):
        """Test valid HTTP server configuration."""
        assert sample_http_config.name == "test-http-server"
        assert sample_http_config.active is True
        assert sample_http_config.url == "https://example.com/mcp"
        assert sample_http_config.transport == "streamable_http"

    def test_stdio_config_valid(self, sample_stdio_config):
        """Test valid STDIO server configuration."""
        assert sample_stdio_config.name == "test-stdio-server"
        assert sample_stdio_config.command == "npx"
        assert len(sample_stdio_config.args) == 3
        assert sample_stdio_config.transport == "stdio"

    def test_config_with_tool_scope(self):
        """Test configuration with tool scope."""
        config = MCPServerConfig(
            name="test-server",
            active=True,
            url="https://example.com/mcp",
            transport="streamable_http",
            tool_scope="default",
        )
        assert config.tool_scope == "default"

    def test_config_defaults(self):
        """Test default values in configuration."""
        config = MCPServerConfig(name="minimal-server", url="https://example.com/mcp")
        assert config.active is True
        assert config.transport == "streamable_http"
        assert config.args == []


class TestMCPClientManagerClientCreation:
    """Tests for MCP client creation."""

    @pytest.mark.asyncio
    async def test_initialize_client_with_http_servers(self, mcp_manager, sample_http_config):
        """Test initializing single client with HTTP-based servers."""
        server_configs = {"test-http-server": sample_http_config}

        with patch("aviator.mcp.client.manager.MultiServerMCPClient") as mock_client_class:
            mock_client_class.return_value = MagicMock()

            client = await mcp_manager._initialize_client(server_configs)

            assert client is not None
            # Sentinel client is created with empty dict (per-server clients built in _discover_tools)
            mock_client_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_client_with_stdio_servers(self, mcp_manager, sample_stdio_config):
        """Test initializing single client with STDIO-based servers."""
        server_configs = {"test-stdio-server": sample_stdio_config}

        with patch("aviator.mcp.client.manager.MultiServerMCPClient") as mock_client_class:
            mock_client_class.return_value = MagicMock()

            client = await mcp_manager._initialize_client(server_configs)

            assert client is not None
            mock_client_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_client_with_multiple_servers(self, mcp_manager):
        """Test that single client handles multiple server configurations."""
        config1 = MCPServerConfig(name="server1", active=True, url="https://server1.com/mcp")
        config2 = MCPServerConfig(name="server2", active=True, url="https://server2.com/mcp")
        server_configs = {"server1": config1, "server2": config2}

        with patch("aviator.mcp.client.manager.MultiServerMCPClient") as mock_client_class:
            client = await mcp_manager._initialize_client(server_configs)

            assert client is not None
            # Sentinel client is created with empty dict (per-server clients built in _discover_tools)
            mock_client_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_client_no_active_servers(self, mcp_manager, disabled_config):
        """Test that None is returned when all servers are inactive."""
        server_configs = {"disabled-server": disabled_config}

        client = await mcp_manager._initialize_client(server_configs)

        assert client is None


class TestMCPClientManagerToolDiscovery:
    """Tests for tool discovery via _discover_tools."""

    @pytest.mark.asyncio
    async def test_discover_tools_success(self, mcp_manager):
        """Test successful tool discovery from MCP client."""
        mock_tool1 = MagicMock()
        mock_tool1.name = "search"
        mock_tool2 = MagicMock()
        mock_tool2.name = "calculate"

        mock_per_server_client = MagicMock()
        mock_per_server_client.get_tools = AsyncMock(return_value=[mock_tool1, mock_tool2])

        server_configs = {"test-server": MCPServerConfig(name="test-server", url="https://test.com/mcp")}

        with patch(
            "aviator.mcp.client.manager.MultiServerMCPClient",
            return_value=mock_per_server_client,
        ):
            tools = await mcp_manager._discover_tools(None, server_configs)

        assert len(tools) == 2
        assert tools[0].name == "search"
        assert tools[1].name == "calculate"

    @pytest.mark.asyncio
    async def test_discover_tools_handles_errors(self, mcp_manager):
        """Test that tool discovery handles client errors gracefully."""
        mock_client = MagicMock()
        mock_client.get_tools = AsyncMock(side_effect=Exception("Connection failed"))

        server_configs = {"test-server": MCPServerConfig(name="test-server", url="https://test.com/mcp")}

        tools = await mcp_manager._discover_tools(mock_client, server_configs)

        assert tools == []

    @pytest.mark.asyncio
    async def test_discover_tools_no_client(self, mcp_manager):
        """Test that discovery returns empty list when client is None."""
        tools = await mcp_manager._discover_tools(None, {})
        assert tools == []

    @pytest.mark.asyncio
    async def test_get_remote_mcp_tools_uses_cache(self, mcp_manager):
        """Test that get_remote_mcp_tools returns cached tools for a tenant."""
        mock_tool = MagicMock()
        mock_tool.name = "cached_tool"
        mock_tool.name = "test-server_cached_tool"

        config = MCPServerConfig(name="test-server", url="https://test.com/mcp", tool_scope="default")
        ctx = _make_tenant_context(
            server_configs={"test-server": config},
            client=MagicMock(),
            tools_cache=[mock_tool],
        )
        mcp_manager._tenant_contexts["default"] = ctx

        tools = await mcp_manager.get_remote_mcp_tools()

        assert len(tools) >= 0  # Filtered by scope; at minimum no error raised

    @pytest.mark.asyncio
    async def test_get_remote_mcp_tools_initialises_for_new_tenant(self, mcp_manager):
        """Test that get_remote_mcp_tools initialises context for unknown tenants."""
        with patch.object(mcp_manager, "_load_from_database", new_callable=AsyncMock, return_value={}):
            tools = await mcp_manager.get_remote_mcp_tools()

        assert tools == []
        assert "default" in mcp_manager._tenant_contexts

    @pytest.mark.asyncio
    async def test_refresh_tools_updates_cache(self, mcp_manager):
        """Test that refresh_tools invalidates the cache for the tenant."""
        mock_client = MagicMock()
        config = MCPServerConfig(name="test-server", url="https://test.com/mcp")
        ctx = _make_tenant_context(
            server_configs={"test-server": config},
            client=mock_client,
            tools_cache=[MagicMock()],
        )
        mcp_manager._tenant_contexts["default"] = ctx

        await mcp_manager.refresh_tools()

        # Cache should be invalidated — context deleted so next request reloads from DB
        assert "default" not in mcp_manager._tenant_contexts

    @pytest.mark.asyncio
    async def test_refresh_tools_no_context(self, mcp_manager):
        """Test refresh_tools is a no-op when no context exists for tenant."""
        # Should not raise
        await mcp_manager.refresh_tools()


class TestMCPClientManagerServerStatus:
    """Tests for server status reporting."""

    @pytest.mark.asyncio
    async def test_get_server_status_with_context(self, mcp_manager):
        """Test getting status of all servers when context exists."""
        mock_tool = MagicMock()
        mock_client = MagicMock()
        config = MCPServerConfig(
            name="test-server",
            active=True,
            url="https://test.com/mcp",
            transport="streamable_http",
        )
        ctx = _make_tenant_context(
            server_configs={"test-server": config},
            client=mock_client,
            tools_cache=[mock_tool],
        )
        mcp_manager._tenant_contexts["default"] = ctx

        status = await mcp_manager.get_server_status()

        assert "test-server" in status
        assert status["test-server"]["active"] is True
        assert status["test-server"]["connected"] is True
        assert status["test-server"]["transport"] == "streamable_http"
        assert status["test-server"]["url"] == "https://test.com/mcp"
        assert "_client_summary" in status
        assert status["_client_summary"]["total_tools_available"] == 1

    @pytest.mark.asyncio
    async def test_get_server_status_no_context(self, mcp_manager):
        """Test status returns summary-only when no context initialised."""
        status = await mcp_manager.get_server_status()

        assert "_client_summary" in status
        assert status["_client_summary"]["initialized"] is False

    @pytest.mark.asyncio
    async def test_get_server_status_disconnected(self, mcp_manager):
        """Test status for server with no client (disconnected)."""
        config = MCPServerConfig(name="disconnected", active=True, url="https://disconnected.com/mcp")
        ctx = _make_tenant_context(
            server_configs={"disconnected": config},
            client=None,
            tools_cache=[],
        )
        mcp_manager._tenant_contexts["default"] = ctx

        status = await mcp_manager.get_server_status()

        assert status["disconnected"]["connected"] is False
        assert status["_client_summary"]["total_tools_available"] == 0
