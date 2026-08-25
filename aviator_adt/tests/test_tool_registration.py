"""Unit tests for MCP tool registration/list/delete REST endpoints."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_orm_row(row_id, key, value, subscription_id):
    """Return a SimpleNamespace that mimics a TenantLookupConfig ORM instance."""
    return SimpleNamespace(
        id=row_id,
        key=key,
        value=value,
        subscription_id=subscription_id,
    )


def _patch_db():
    """Patch database_manager.session() context manager in mcp_service."""
    mock_session = MagicMock()

    @contextmanager
    def _mock_session(schema_name=None):
        yield mock_session

    return patch("aviator.services.mcp_service.database_manager.session", _mock_session)


class TestRegisterTool:
    """Tests for POST /mcp-server/register/tools endpoint."""

    _valid_payload = {"tools": ["current_time"]}

    def test_register_tool_success(self, client):
        """Test successful tool registration."""
        orm_row = _make_orm_row(
            1,
            "ALLOWED_MCP_TOOLS",
            [{"name": "current_time", "description": "Current time tool"}],
            None,
        )
        mock_tool = MagicMock()
        mock_tool.description = "Current time tool"

        with (
            _patch_db(),
            patch("aviator.services.mcp_service.discover_tools", return_value={"current_time": mock_tool}),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
            patch("aviator.services.mcp_service.tenant_id_to_schema_name", return_value="public"),
        ):
            mock_repo_cls.return_value.create_or_update.return_value = orm_row
            response = client.post("/mcp-server/register/tools", json=self._valid_payload)

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == 1
        assert data["key"] == "ALLOWED_MCP_TOOLS"
        assert data["subscription_id"] is None

    def test_register_tool_missing_tools(self, client):
        """Test registration fails without tools list."""
        response = client.post("/mcp-server/register/tools", json={})
        assert response.status_code == 422

    def test_register_tool_no_available_tools(self, client):
        """Test registration returns 400 when no requested tools exist."""
        with (
            _patch_db(),
            patch("aviator.services.mcp_service.discover_tools", return_value={}),
            patch("aviator.services.mcp_service.tenant_id_to_schema_name", return_value="public"),
        ):
            response = client.post("/mcp-server/register/tools", json=self._valid_payload)

        assert response.status_code == 400
        assert "None of the requested tools are available" in response.json()["detail"]

    def test_register_tool_value_error(self, client):
        """Test registration returns 400 on ValueError."""
        with (
            _patch_db(),
            patch(
                "aviator.services.mcp_service.discover_tools",
                return_value={"current_time": MagicMock(description="tool")},
            ),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
            patch("aviator.services.mcp_service.tenant_id_to_schema_name", return_value="public"),
        ):
            mock_repo_cls.return_value.create_or_update.side_effect = ValueError("Invalid input")
            response = client.post("/mcp-server/register/tools", json=self._valid_payload)

        assert response.status_code == 400
        assert "Invalid input" in response.json()["detail"]

    def test_register_tool_database_error(self, client):
        """Test registration returns 500 on database error."""
        with (
            _patch_db(),
            patch(
                "aviator.services.mcp_service.discover_tools",
                return_value={"current_time": MagicMock(description="tool")},
            ),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
            patch("aviator.services.mcp_service.tenant_id_to_schema_name", return_value="public"),
        ):
            mock_repo_cls.return_value.create_or_update.side_effect = Exception("Database connection failed")
            response = client.post("/mcp-server/register/tools", json=self._valid_payload)

        assert response.status_code == 500
        assert "Failed to register tool config" in response.json()["detail"]


class TestListTools:
    """Tests for GET /mcp-server/list/tools endpoint."""

    def test_list_tools_from_database(self, client):
        """Test listing tools from database using public context."""
        rows = [
            _make_orm_row(1, "ALLOWED_MCP_TOOLS", '["tool_a", "tool_b"]', None),
            _make_orm_row(2, "ALLOWED_MCP_TOOLS", '["tool_c"]', None),
        ]

        with (
            _patch_db(),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
        ):
            mock_repo_cls.return_value.get.return_value = rows
            response = client.get("/mcp-server/list/tools")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[1]["id"] == 2

    def test_list_tools_from_database_empty(self, client):
        """Test listing tools returns empty list when no results."""
        with (
            _patch_db(),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
        ):
            mock_repo_cls.return_value.get.return_value = []
            response = client.get("/mcp-server/list/tools")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_tools_database_error(self, client):
        """Test listing tools returns 500 on database error."""
        with (
            _patch_db(),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
        ):
            mock_repo_cls.return_value.get.side_effect = Exception("Database error")
            response = client.get("/mcp-server/list/tools")

        assert response.status_code == 500
        assert "Failed to fetch tools" in response.json()["detail"]


class TestDeleteTool:
    """Tests for DELETE /mcp-server/tools endpoint."""

    _registered_row = _make_orm_row(
        1,
        "ALLOWED_MCP_TOOLS",
        '[{"name": "current_time", "description": "Current Time"}, {"name": "date_time", "description": "Date Time Tool"}]',
        None,
    )

    def test_delete_tool_success(self, client):
        """Test successful tool removal with remaining tools in config."""
        updated_row = _make_orm_row(
            1,
            "ALLOWED_MCP_TOOLS",
            '[{"name": "date_time", "description": "Date Time Tool"}]',
            None,
        )

        with (
            _patch_db(),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
            patch("aviator.services.mcp_service.tenant_id_to_schema_name", return_value="public"),
        ):
            mock_repo = mock_repo_cls.return_value
            mock_repo.get.return_value = [self._registered_row]
            mock_repo.remove_tools.return_value = updated_row
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["current_time"]})

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Tools removed successfully."
        assert data["subscription_id"] is None
        assert data["removed_tools"] == ["current_time"]
        assert len(data["remaining_tools"]) == 1

    def test_delete_tool_all_removed(self, client):
        """Test deletion path when all tools are removed and row is deleted."""
        with (
            _patch_db(),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
            patch("aviator.services.mcp_service.tenant_id_to_schema_name", return_value="public"),
        ):
            mock_repo = mock_repo_cls.return_value
            mock_repo.get.return_value = [self._registered_row]
            mock_repo.remove_tools.return_value = None
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["current_time", "date_time"]})

        assert response.status_code == 200
        assert "Config entry deleted" in response.json()["message"]

    def test_delete_tool_no_registered_tools(self, client):
        """Test deletion returns 200 when no tools are registered."""
        with (
            _patch_db(),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
            patch("aviator.services.mcp_service.tenant_id_to_schema_name", return_value="public"),
        ):
            mock_repo_cls.return_value.get.return_value = []
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["current_time"]})

        assert response.status_code == 200
        assert "No registered tools to remove" in response.json()["message"]

    def test_delete_tool_not_registered(self, client):
        """Test deletion returns 404 when requested tool is not in registered list."""
        with (
            _patch_db(),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
            patch("aviator.services.mcp_service.tenant_id_to_schema_name", return_value="public"),
        ):
            mock_repo_cls.return_value.get.return_value = [self._registered_row]
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["nonexistent_tool"]})

        assert response.status_code == 404
        assert "Tool(s) not registered" in response.json()["detail"]
        assert "nonexistent_tool" in response.json()["detail"]

    def test_delete_tool_missing_tools_body(self, client):
        """Test deletion fails without tools payload."""
        response = client.request("DELETE", "/mcp-server/tools", json={})
        assert response.status_code == 422

    def test_delete_tool_database_error(self, client):
        """Test deletion returns 500 on database error."""
        with (
            _patch_db(),
            patch("aviator.services.mcp_service.TenantLookupRepository") as mock_repo_cls,
            patch("aviator.services.mcp_service.tenant_id_to_schema_name", return_value="public"),
        ):
            mock_repo = mock_repo_cls.return_value
            mock_repo.get.return_value = [self._registered_row]
            mock_repo.remove_tools.side_effect = Exception("Database error")
            response = client.request("DELETE", "/mcp-server/tools", json={"tools": ["current_time"]})

        assert response.status_code == 500
        assert "Failed to remove tools" in response.json()["detail"]
