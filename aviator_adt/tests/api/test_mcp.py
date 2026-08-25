"""Test suite for MCP API endpoints."""

import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from aviator.exceptions import MCPToolAlreadyRegisteredError
from aviator.services.mcp_service import MCPServerService

os.environ["VECTOR_STORE"] = "memory"
os.environ["CHECKPOINTER"] = "memory"
os.environ["USAGE_TRACKING_ENABLED"] = "false"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lookup_row(row_id, key, value, subscription_id):
    """Return a SimpleNamespace that mimics a TenantLookupConfig ORM instance."""
    return SimpleNamespace(
        id=row_id,
        key=key,
        value=value,
        subscription_id=subscription_id,
    )


@contextmanager
def _fake_session():
    """Return a lightweight fake DB session context manager."""
    yield SimpleNamespace()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create test client with authentication headers."""
    headers = {"auth-ticket": "some-valid-ticket"}

    from aviator.main import app

    with TestClient(app) as client:
        client.headers.update(headers)
        yield client


@pytest.fixture
def mock_mcp_client_manager():
    """Mock MCP client manager for testing."""
    with patch("aviator.mcp.client.router.mcp_client_manager") as mock:
        yield mock


# ---------------------------------------------------------------------------
# Server CRUD - GET /mcp/servers
# ---------------------------------------------------------------------------


class TestGetMCPServers:
    """Tests for GET /mcp/servers endpoint."""

    def test_get_mcp_servers_success(self, client):
        """Test successful retrieval of MCP servers from database."""
        from aviator.mcp.models import MCPServerConfigResponse

        servers = [
            MCPServerConfigResponse(
                id="uuid-1",
                name="tavily",
                active=True,
                transport="streamable_http",
                url="https://mcp.tavily.com/mcp",
            ),
            MCPServerConfigResponse(
                id="uuid-2",
                name="filesystem",
                active=True,
                transport="stdio",
            ),
        ]

        with patch("aviator.mcp.client.router.mcp_client_service.list_server_configs", return_value=servers):
            response = client.get("/mcp-client/servers")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] in ["tavily", "filesystem"]
        assert data[0]["active"] is True
        assert data[0]["id"] in ["uuid-1", "uuid-2"]

    def test_get_mcp_servers_empty(self, client):
        """Test getting MCP servers when none are configured."""
        with patch("aviator.mcp.client.router.mcp_client_service.list_server_configs", return_value=[]):
            response = client.get("/mcp-client/servers")

        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------------------
# Server CRUD - POST /mcp/servers
# ---------------------------------------------------------------------------


class TestCreateMCPServer:
    """Tests for POST /mcp/servers endpoint."""

    _valid_payload = {
        "name": "tavily",
        "transport": "streamable_http",
        "url": "https://mcp.tavily.com/mcp",
        "active": True,
    }

    def test_create_server_success(self, client):
        """Test successful server creation returns 201 and echoes back the payload."""
        from aviator.mcp.models import MCPServerConfigResponse

        server = MCPServerConfigResponse(
            id="test-uuid-123",
            name="tavily",
            active=True,
            transport="streamable_http",
            url="https://mcp.tavily.com/mcp",
        )

        with patch("aviator.mcp.client.router.mcp_client_service.create_server_config", return_value=server):
            response = client.post("/mcp-client/servers", json=self._valid_payload)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "tavily"
        assert data["active"] is True
        assert data["id"] == "test-uuid-123"

    def test_create_server_conflict(self, client):
        """Test that a duplicate host raises 409."""
        with patch(
            "aviator.mcp.client.router.mcp_client_service.create_server_config",
            side_effect=HTTPException(status_code=status.HTTP_409_CONFLICT, detail="duplicate host"),
        ):
            response = client.post("/mcp-client/servers", json=self._valid_payload)

        assert response.status_code == 409
        assert "duplicate host" in response.json()["detail"]

    def test_create_server_missing_name(self, client):
        """Test that omitting required ''name'' returns 422."""
        payload = {k: v for k, v in self._valid_payload.items() if k != "name"}
        response = client.post("/mcp-client/servers", json=payload)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Server CRUD - GET /mcp/servers/{server_id}
# ---------------------------------------------------------------------------


class TestGetMCPServer:
    """Tests for GET /mcp/servers/{server_id} endpoint."""

    def test_get_mcp_server_success(self, client):
        """Test successful retrieval of a specific MCP server."""
        from aviator.mcp.models import MCPServerConfigResponse

        server = MCPServerConfigResponse(
            id="tavily-uuid",
            name="tavily",
            active=True,
            transport="streamable_http",
            url="https://mcp.tavily.com/mcp",
        )

        with patch("aviator.mcp.client.router.mcp_client_service.get_server_config", return_value=server):
            response = client.get("/mcp-client/servers/tavily")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "tavily"
        assert data["active"] is True
        assert data["id"] == "tavily-uuid"

    def test_get_mcp_server_not_found(self, client):
        """Test getting a non-existent MCP server returns 404."""
        with patch(
            "aviator.mcp.client.router.mcp_client_service.get_server_config",
            side_effect=HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found"),
        ):
            response = client.get("/mcp-client/servers/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Server CRUD - PUT /mcp/servers/{server_id}
# ---------------------------------------------------------------------------


class TestUpdateMCPServer:
    """Tests for PUT /mcp/servers/{server_id} endpoint."""

    _valid_payload = {
        "name": "tavily",
        "transport": "streamable_http",
        "url": "https://mcp.tavily.com/mcp",
        "active": False,
    }

    def test_update_server_success(self, client):
        """Test successful server update."""
        from aviator.mcp.models import MCPServerConfigResponse

        updated = MCPServerConfigResponse(
            id="some-uuid",
            name="tavily",
            active=False,
            transport="streamable_http",
            url="https://mcp.tavily.com/mcp",
        )

        with patch("aviator.mcp.client.router.mcp_client_service.update_server_config", return_value=updated):
            response = client.put("/mcp-client/servers/some-uuid", json=self._valid_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "tavily"
        assert data["id"] == "some-uuid"

    def test_update_server_not_found(self, client):
        """Test update returns 404 when server does not exist."""
        with patch(
            "aviator.mcp.client.router.mcp_client_service.update_server_config",
            side_effect=HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found"),
        ):
            response = client.put("/mcp-client/servers/missing-uuid", json=self._valid_payload)

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_server_conflict(self, client):
        """Test update returns 409 on duplicate-host conflict."""
        with patch(
            "aviator.mcp.client.router.mcp_client_service.update_server_config",
            side_effect=HTTPException(status_code=status.HTTP_409_CONFLICT, detail="duplicate host"),
        ):
            response = client.put("/mcp-client/servers/some-uuid", json=self._valid_payload)

        assert response.status_code == 409
        assert "duplicate host" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Server CRUD - DELETE /mcp/servers/{server_id}
# ---------------------------------------------------------------------------


class TestDeleteMCPServer:
    """Tests for DELETE /mcp/servers/{server_id} endpoint."""

    def test_delete_server_success(self, client):
        """Test successful server deletion."""
        from aviator.mcp.models import MCPServerConfigResponse

        server = MCPServerConfigResponse(
            id="some-uuid",
            name="tavily",
            active=True,
            transport="streamable_http",
            url="https://mcp.tavily.com/mcp",
        )

        with patch("aviator.mcp.client.router.mcp_client_service.delete_server_config", return_value=server):
            response = client.delete("/mcp-client/servers/some-uuid")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "tavily"
        assert data["id"] == "some-uuid"

    def test_delete_server_not_found(self, client):
        """Test deletion returns 404 when server does not exist."""
        with patch(
            "aviator.mcp.client.router.mcp_client_service.delete_server_config",
            side_effect=HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found"),
        ):
            response = client.delete("/mcp-client/servers/missing-uuid")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_delete_server_rejects_body(self, client):
        """Test that a DELETE request with a body is rejected with 400."""
        response = client.request("DELETE", "/mcp-client/servers/some-uuid", json={"key": "value"})

        assert response.status_code == 400
        assert "not allowed" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# MCP remote tools - GET /mcp/tools
# ---------------------------------------------------------------------------


class TestGetMCPTools:
    """Tests for GET /mcp/tools endpoint."""

    def test_get_mcp_tools_success(self, client, mock_mcp_client_manager):
        """Test successful retrieval of MCP tools."""
        mock_tool1 = MagicMock()
        mock_tool1.name = "tavily_search"
        mock_tool1.description = "Search the web"
        mock_tool1.args_schema = None

        mock_tool2 = MagicMock()
        mock_tool2.name = "filesystem_read"
        mock_tool2.description = "Read files"
        mock_tool2.args_schema = None

        mock_mcp_client_manager.get_remote_mcp_tools = AsyncMock(return_value=[mock_tool1, mock_tool2])

        response = client.get("/mcp-client/tools")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] in ["tavily_search", "filesystem_read"]
        assert "description" in data[0]

    def test_get_mcp_tools_empty(self, client, mock_mcp_client_manager):
        """Test getting MCP tools when none are available."""
        mock_mcp_client_manager.get_remote_mcp_tools = AsyncMock(return_value=[])

        response = client.get("/mcp-client/tools")

        assert response.status_code == 200
        assert response.json() == []

    def test_get_mcp_tools_error(self, client, mock_mcp_client_manager):
        """Test error handling when getting MCP tools fails."""
        mock_mcp_client_manager.get_remote_mcp_tools = AsyncMock(side_effect=Exception("Tool discovery failed"))

        response = client.get("/mcp-client/tools")

        assert response.status_code == 500
        assert "Failed to retrieve MCP tools" in response.json()["detail"]


# ---------------------------------------------------------------------------
# MCP remote tools - POST /mcp/tools/refresh
# ---------------------------------------------------------------------------


class TestRefreshMCPTools:
    """Tests for POST /mcp/tools/refresh endpoint."""

    def test_refresh_mcp_tools_success(self, client, mock_mcp_client_manager):
        """Test successful refresh of MCP tools."""
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"

        mock_mcp_client_manager.refresh_tools = AsyncMock()
        mock_mcp_client_manager.get_remote_mcp_tools = AsyncMock(return_value=[mock_tool])

        response = client.post("/mcp-client/tools/refresh")

        assert response.status_code == 200
        data = response.json()
        assert "MCP tools refreshed" in data["message"]
        assert "1 tools available" in data["message"]
        mock_mcp_client_manager.refresh_tools.assert_called_once()
        mock_mcp_client_manager.get_remote_mcp_tools.assert_called_once()

    def test_refresh_mcp_tools_error(self, client, mock_mcp_client_manager):
        """Test error handling when refreshing MCP tools fails."""
        mock_mcp_client_manager.refresh_tools = AsyncMock(side_effect=Exception("Refresh failed"))

        response = client.post("/mcp-client/tools/refresh")

        assert response.status_code == 500
        assert "Failed to refresh MCP tools" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Health - GET /mcp/health  (mcp_client_manager-based)
# ---------------------------------------------------------------------------


class TestMCPHealthCheck:
    """Tests for GET /mcp/health endpoint."""

    def test_mcp_health_check_healthy(self, client, mock_mcp_client_manager):
        """Test MCP health check when all servers are connected."""
        mock_mcp_client_manager.get_server_status = AsyncMock(
            return_value={
                "server1": {
                    "active": True,
                    "connected": True,
                    "transport": "streamable_http",
                    "url": "https://s1.com/mcp",
                    "tool_count": 5,
                },
                "server2": {
                    "active": True,
                    "connected": True,
                    "transport": "stdio",
                    "url": None,
                    "tool_count": 3,
                },
            }
        )

        response = client.get("/mcp-client/health")

        assert response.status_code == 200
        data = response.json()
        assert data["mcp_enabled"] is True
        assert data["total_servers"] == 2
        assert data["connected_servers"] == 2
        assert data["total_tools"] == 8
        assert data["health_status"] == "healthy"

    def test_mcp_health_check_partial(self, client, mock_mcp_client_manager):
        """Test MCP health check when some servers are disconnected."""
        mock_mcp_client_manager.get_server_status = AsyncMock(
            return_value={
                "server1": {
                    "active": True,
                    "connected": True,
                    "transport": "streamable_http",
                    "url": "https://s1.com/mcp",
                    "tool_count": 5,
                },
                "server2": {
                    "active": True,
                    "connected": False,
                    "transport": "stdio",
                    "url": None,
                    "tool_count": 0,
                },
            }
        )

        response = client.get("/mcp-client/health")

        assert response.status_code == 200
        data = response.json()
        assert data["mcp_enabled"] is True
        assert data["total_servers"] == 2
        assert data["connected_servers"] == 1
        assert data["total_tools"] == 5
        assert data["health_status"] == "partial"

    def test_mcp_health_check_error(self, client, mock_mcp_client_manager):
        """Test MCP health check when an error occurs."""
        mock_mcp_client_manager.get_server_status = AsyncMock(side_effect=Exception("Health check failed"))

        response = client.get("/mcp-client/health")

        assert response.status_code == 200
        data = response.json()
        assert data["mcp_enabled"] is False
        assert data["health_status"] == "unhealthy"
        assert "error" in data


# ---------------------------------------------------------------------------
# Health - GET /mcp/server/health  (service-based)
# ---------------------------------------------------------------------------


class TestMCPServerHealthCheck:
    """Tests for GET /mcp/server/health endpoint."""

    def test_mcp_server_health_healthy(self, client):
        """Test server-side health check returns healthy with discovered tool count."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.get_all_discovered_tools",
            return_value=[{"name": "tool_a", "description": "A"}, {"name": "tool_b", "description": "B"}],
        ):
            response = client.get("/mcp-server/health")

        assert response.status_code == 200
        data = response.json()
        assert data["mcp_enabled"] is True
        assert data["total_tools"] == 2
        assert data["health_status"] == "healthy"

    def test_mcp_server_health_empty(self, client):
        """Test server-side health check with no discovered tools."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.get_all_discovered_tools",
            return_value=[],
        ):
            response = client.get("/mcp-server/health")

        assert response.status_code == 200
        data = response.json()
        assert data["mcp_enabled"] is True
        assert data["total_tools"] == 0
        assert data["health_status"] == "healthy"

    def test_mcp_server_health_error(self, client):
        """Test server-side health check returns unhealthy on exception."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.get_all_discovered_tools",
            side_effect=Exception("discovery failed"),
        ):
            response = client.get("/mcp-server/health")

        assert response.status_code == 200
        data = response.json()
        assert data["mcp_enabled"] is False
        assert data["health_status"] == "unhealthy"
        assert "error" in data


# ---------------------------------------------------------------------------
# Tool registration - POST /mcp/server/register/tools
# ---------------------------------------------------------------------------


class TestRegisterMCPTool:
    """Tests for POST /mcp/server/register/tools endpoint."""

    _valid_payload = {"tools": ["current_time"]}

    def test_register_tool_success(self, client):
        """Test successful tool-config registration returns 201."""
        orm_row = _make_lookup_row(
            1,
            "ALLOWED_MCP_TOOLS",
            [{"name": "current_time", "description": "Current time tool"}],
            None,
        )

        with patch("aviator.mcp.server.router.mcp_server_service.register_tools", return_value=orm_row):
            response = client.post("/mcp-server/register/tools", json=self._valid_payload)

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == 1
        assert data["key"] == "ALLOWED_MCP_TOOLS"
        assert data["subscription_id"] is None

    def test_register_tool_missing_tools(self, client):
        """Test registration without tools list returns 422."""
        response = client.post("/mcp-server/register/tools", json={})
        assert response.status_code == 422

    def test_register_tool_no_available_tools(self, client):
        """Test registration returns 400 when requested tools are unavailable."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.register_tools",
            side_effect=HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="None of the requested tools are available",
            ),
        ):
            response = client.post("/mcp-server/register/tools", json=self._valid_payload)

        assert response.status_code == 400
        assert "None of the requested tools are available" in response.json()["detail"]

    def test_register_tool_value_error(self, client):
        """Test registration returns 400 on validation error."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.register_tools",
            side_effect=HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid input"),
        ):
            response = client.post("/mcp-server/register/tools", json=self._valid_payload)

        assert response.status_code == 400
        assert "Invalid input" in response.json()["detail"]

    def test_register_tool_already_exists(self, client):
        """Test registration returns 409 when the tool is already registered."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.register_tools",
            side_effect=HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tool(s) already registered: current_time",
            ),
        ):
            response = client.post("/mcp-server/register/tools", json=self._valid_payload)

        assert response.status_code == 409
        assert "already registered" in response.json()["detail"]

    def test_register_tool_database_error(self, client):
        """Test registration returns 500 on unexpected DB error."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.register_tools",
            side_effect=HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to register tool config",
            ),
        ):
            response = client.post("/mcp-server/register/tools", json=self._valid_payload)

        assert response.status_code == 500
        assert "Failed to register tool config" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Tool listing - GET /mcp/server/list/tools
# ---------------------------------------------------------------------------


class TestListMCPRegisteredTools:
    """Tests for GET /mcp/server/list/tools endpoint."""

    def test_list_tools_with_body_returns_400(self, client):
        """GET /list/tools must reject request bodies."""
        response = client.request("GET", "/mcp-server/list/tools", json={"tools": ["current_time"]})

        assert response.status_code == 400
        assert response.json()["detail"] == "GET request body is not allowed"

    def test_list_tools_from_database(self, client):
        """Test listing tools returns rows with id, key, value, subscription_id."""
        rows = [
            {"id": 1, "key": "ALLOWED_MCP_TOOLS", "value": ["tool_a", "tool_b"], "subscription_id": None},
            {"id": 2, "key": "ALLOWED_MCP_TOOLS", "value": ["tool_c"], "subscription_id": None},
        ]

        with patch("aviator.mcp.server.router.mcp_server_service.list_registered_tools", return_value=rows):
            response = client.get("/mcp-server/list/tools")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[1]["id"] == 2

    def test_list_tools_response_excludes_legacy_tenant_id(self, client):
        """Test response shape does not include legacy tenant_id field."""
        rows = [{"id": 1, "key": "ALLOWED_MCP_TOOLS", "value": "tool_a", "subscription_id": None}]

        with patch("aviator.mcp.server.router.mcp_server_service.list_registered_tools", return_value=rows):
            response = client.get("/mcp-server/list/tools")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert "tenant_id" not in data[0]
        assert data[0]["subscription_id"] is None

    def test_list_tools_from_database_empty(self, client):
        """Test listing tools returns 404 when no rows are found."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.list_registered_tools",
            side_effect=HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No registered tools found for this subscription",
            ),
        ):
            response = client.get("/mcp-server/list/tools")

        assert response.status_code == 404
        assert "No registered tools found" in response.json()["detail"]

    def test_list_tools_database_error(self, client):
        """Test that an unexpected DB error returns 500."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.list_registered_tools",
            side_effect=HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch tools",
            ),
        ):
            response = client.get("/mcp-server/list/tools")

        assert response.status_code == 500
        assert "Failed to fetch tools" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Tool deletion - DELETE /mcp/server/tools
# ---------------------------------------------------------------------------


class TestDeleteMCPRegisteredTool:
    """Tests for DELETE /mcp/server/tools endpoint."""

    def test_delete_tool_success(self, client):
        """Test successful removal of tools from a registered tool config."""
        result = {
            "message": "Tools removed successfully.",
            "subscription_id": None,
            "removed_tools": ["current_time"],
            "remaining_tools": [{"name": "date_time", "description": "Date Time Tool"}],
        }

        with patch("aviator.mcp.server.router.mcp_server_service.delete_tools", return_value=result):
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["current_time"]})

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Tools removed successfully."
        assert data["subscription_id"] is None
        assert data["removed_tools"] == ["current_time"]
        assert len(data["remaining_tools"]) == 1

    def test_delete_tool_all_removed(self, client):
        """Test removal returns 200 when all tools are removed and row is deleted."""
        result = {
            "message": "Tools removed. Config entry deleted as no tools remain.",
            "removed_tools": ["current_time", "date_time"],
            "subscription_id": None,
        }

        with patch("aviator.mcp.server.router.mcp_server_service.delete_tools", return_value=result):
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["current_time", "date_time"]})

        assert response.status_code == 200
        assert "Config entry deleted" in response.json()["message"]

    def test_delete_tool_no_registered_tools(self, client):
        """Test deletion returns 404 when no tools are registered."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.delete_tools",
            side_effect=HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No registered tools found for this subscription",
            ),
        ):
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["current_time"]})

        assert response.status_code == 404
        assert "No registered tools found" in response.json()["detail"]

    def test_delete_tool_not_registered(self, client):
        """Test deletion returns 404 when requested tool is not in registered list."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.delete_tools",
            side_effect=HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tool(s) not registered: nonexistent_tool",
            ),
        ):
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["nonexistent_tool"]})

        assert response.status_code == 404
        assert "Tool(s) not registered" in response.json()["detail"]
        assert "nonexistent_tool" in response.json()["detail"]

    def test_delete_tool_missing_tools_body(self, client):
        """Test deletion without tools list returns 422."""
        response = client.request("DELETE", "/mcp-server/tools", json={})
        assert response.status_code == 422

    def test_delete_tool_database_error(self, client):
        """Test that an unexpected DB error returns 500."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.delete_tools",
            side_effect=HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to remove tools",
            ),
        ):
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["current_time"]})

        assert response.status_code == 500
        assert "Failed to remove tools" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Tool discovery listing - GET /mcp/server/list/alltools
# ---------------------------------------------------------------------------


class TestListAllDiscoveredTools:
    """Tests for GET /mcp/server/list/alltools endpoint."""

    def test_list_all_discovered_tools_with_body_returns_400(self, client):
        """GET /list/alltools must reject request bodies."""
        response = client.request("GET", "/mcp-server/list/alltools", json={"unexpected": "payload"})

        assert response.status_code == 400
        assert response.json()["detail"] == "GET request body is not allowed"

    def test_list_all_discovered_tools_success(self, client):
        """Test listing all discovered tools."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.get_all_discovered_tools",
            return_value=[{"name": "tool_a", "description": "A tool"}, {"name": "tool_b", "description": "B tool"}],
        ):
            response = client.get("/mcp-server/list/alltools")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert any(item["name"] == "tool_a" for item in data)
        assert any(item["name"] == "tool_b" for item in data)

    def test_list_all_discovered_tools_error(self, client):
        """Test discovery endpoint returns 500 on discovery failure."""
        with patch(
            "aviator.mcp.server.router.mcp_server_service.get_all_discovered_tools",
            side_effect=Exception("boom"),
        ):
            response = client.get("/mcp-server/list/alltools")

        assert response.status_code == 500
        assert "Failed to discover tools" in response.json()["detail"]


# ---------------------------------------------------------------------------
# MCP server service coverage
# ---------------------------------------------------------------------------


class TestMCPServerService:
    """Unit tests for MCPServerService edge cases not covered at router level."""

    def test_register_tools_duplicate_maps_to_conflict(self) -> None:
        """Repeated registrations should surface as HTTP 409 Conflict."""
        service = MCPServerService()

        with (
            patch(
                "aviator.services.mcp_service.discover_tools",
                return_value={"current_time": SimpleNamespace(description="Current time")},
            ),
            patch("aviator.services.mcp_service.database_manager.session", return_value=_fake_session()),
            patch(
                "aviator.services.mcp_service.TenantLookupRepository.create_or_update",
                side_effect=MCPToolAlreadyRegisteredError("Tool(s) already registered: current_time"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.register_tools(["current_time"], user={})

        assert exc_info.value.status_code == 409
        assert "already registered" in str(exc_info.value.detail)

    def test_register_tools_response_contains_only_requested_tools(self) -> None:
        """Register response should echo only the current request payload."""
        service = MCPServerService()
        merged_row = _make_lookup_row(
            1,
            "ALLOWED_MCP_TOOLS",
            [
                {"name": "current_time", "description": "Current time"},
                {"name": "date_time", "description": "Date time"},
            ],
            None,
        )

        with (
            patch(
                "aviator.services.mcp_service.discover_tools",
                return_value={"date_time": SimpleNamespace(description="Date time")},
            ),
            patch("aviator.services.mcp_service.database_manager.session", return_value=_fake_session()),
            patch(
                "aviator.services.mcp_service.TenantLookupRepository.create_or_update",
                return_value=merged_row,
            ),
        ):
            result = service.register_tools(["date_time"], user={})

        assert result["id"] == 1
        assert result["key"] == "ALLOWED_MCP_TOOLS"
        assert result["subscription_id"] is None
        assert result["value"] == [{"name": "date_time", "description": "Date time"}]

    def test_register_tools_mixed_duplicate_and_new_maps_to_conflict(self) -> None:
        """Mixed requests must fail when any requested tool is already registered."""
        service = MCPServerService()

        with (
            patch(
                "aviator.services.mcp_service.discover_tools",
                return_value={
                    "current_time": SimpleNamespace(description="Current time"),
                    "date_time": SimpleNamespace(description="Date time"),
                },
            ),
            patch("aviator.services.mcp_service.database_manager.session", return_value=_fake_session()),
            patch(
                "aviator.services.mcp_service.TenantLookupRepository.create_or_update",
                side_effect=MCPToolAlreadyRegisteredError("Tool(s) already registered: current_time"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.register_tools(["current_time", "date_time"], user={})

        assert exc_info.value.status_code == 409
        assert "current_time" in str(exc_info.value.detail)

    def test_register_tools_mixed_valid_and_invalid_raises_bad_request(self) -> None:
        """Mixed valid and invalid tool names must fail with 400."""
        service = MCPServerService()

        with (
            patch(
                "aviator.services.mcp_service.discover_tools",
                return_value={"current_time": SimpleNamespace(description="Current time")},
            ),
            patch("aviator.services.mcp_service.database_manager.session", return_value=_fake_session()),
            patch("aviator.services.mcp_service.TenantLookupRepository.create_or_update") as mock_create_or_update,
            pytest.raises(HTTPException) as exc_info,
        ):
            service.register_tools(["current_time", "invalid_tool"], user={})

        assert exc_info.value.status_code == 400
        assert "Invalid tool(s) requested" in str(exc_info.value.detail)
        assert "invalid_tool" in str(exc_info.value.detail)
        mock_create_or_update.assert_not_called()
