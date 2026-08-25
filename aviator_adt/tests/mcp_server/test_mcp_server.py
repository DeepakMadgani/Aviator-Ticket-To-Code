"""Unit tests for aviator.mcp.server.mcp_server (AviatorMCPServer)."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_tool(name: str = "test_tool") -> MagicMock:
    """Return a MagicMock that looks like a BaseTool."""
    mock = MagicMock()
    mock.name = name
    return mock


def _make_mcp_tool_list(names: list[str]):
    """Produce a list of MagicMock objects with .name set, simulating FastMCP tool list."""
    tools = []
    for n in names:
        t = MagicMock()
        t.name = n
        tools.append(t)
    return tools


# ---------------------------------------------------------------------------
# AviatorMCPServer.__init__
# ---------------------------------------------------------------------------


class TestAviatorMCPServerInit:
    """Tests for AviatorMCPServer initialisation."""

    def test_init_discovers_tools(self):
        """__init__ should call discover_tools and store the result."""
        fake_tools = {
            "rag_query": _simple_tool("rag_query"),
            "current_time": _simple_tool("current_time"),
        }

        with patch("aviator.mcp.server.mcp_server.discover_tools", return_value=fake_tools):
            from aviator.mcp.server.mcp_server import AviatorMCPServer

            server = AviatorMCPServer()

        assert server.tools_map == fake_tools
        assert len(server.tools_map) == 2


# ---------------------------------------------------------------------------
# AviatorMCPServer._create_mcp_app — tool registration
# ---------------------------------------------------------------------------


class TestCreateMcpAppRegistration:
    """Tests for _create_mcp_app tool conversion and registration."""

    def _server_with_tools(self, names: list[str]):
        tools = {n: _simple_tool(n) for n in names}
        with patch("aviator.mcp.server.mcp_server.discover_tools", return_value=tools):
            from aviator.mcp.server.mcp_server import AviatorMCPServer

            return AviatorMCPServer()

    def test_tools_converted_via_langchain_tool_to_mcp(self):
        """Every tool in tools_map should be passed through langchain_tool_to_mcp."""
        server = self._server_with_tools(["tool_a", "tool_b"])

        fake_callable = MagicMock()
        with (
            patch(
                "aviator.mcp.server.mcp_server.langchain_tool_to_mcp",
                return_value=fake_callable,
            ) as mock_convert,
            patch("aviator.mcp.server.mcp_server.FastMCP") as mock_fastmcp,
        ):
            mock_fastmcp.return_value = MagicMock()
            server._create_mcp_app()

        assert mock_convert.call_count == 2

    def test_tools_with_no_callable_are_skipped(self):
        """If langchain_tool_to_mcp returns None the tool should not be registered."""
        server = self._server_with_tools(["bad_tool"])

        with (
            patch("aviator.mcp.server.mcp_server.langchain_tool_to_mcp", return_value=None),
            patch("aviator.mcp.server.mcp_server.FastMCP") as mock_fastmcp,
        ):
            mock_fastmcp.return_value = MagicMock()
            server._create_mcp_app()

        _, kwargs = mock_fastmcp.call_args
        assert kwargs.get("tools") == []


# ---------------------------------------------------------------------------
# _list_tools override — tenant filtering
# ---------------------------------------------------------------------------


class TestListToolsOverride:
    """Tests for the _list_tools override applied in _create_mcp_app."""

    @contextmanager
    def _build_app(self, user: dict, all_tool_names: list[str], allowed: set | None):
        """Context manager that keeps patches alive for the duration of the test.

        The override closures in _create_mcp_app reference module-level symbols
        (resolve_user_from_request, get_tools_for_tenant) which are looked up at
        *call time*.  Patches must therefore stay active while the app's
        _list_tools / call_tool are invoked.
        """
        from aviator.mcp.server.mcp_server import AviatorMCPServer

        fake_mcp_tools = _make_mcp_tool_list(all_tool_names)

        class FakeFastMCP:
            def __init__(self_inner, name, tools):  # noqa: N805
                pass

            def add_middleware(self_inner, middleware):  # noqa: N805
                pass

            async def _list_tools(self_inner):  # noqa: N805
                return fake_mcp_tools

            async def call_tool(self_inner, name, arguments=None, **kwargs):  # noqa: N805
                return "tool_result"

            def run(self_inner, **kwargs):  # noqa: N805
                pass

        fake_instance = FakeFastMCP(name="test", tools=[])

        with patch(
            "aviator.mcp.server.mcp_server.discover_tools",
            return_value={n: _simple_tool(n) for n in all_tool_names},
        ):
            server = AviatorMCPServer()

        with (
            patch(
                "aviator.mcp.server.mcp_server.resolve_user_from_request",
                return_value=user,
            ),
            patch(
                "aviator.mcp.server.mcp_server.get_tools_for_tenant",
                return_value=allowed,
            ),
            patch(
                "aviator.mcp.server.mcp_server.langchain_tool_to_mcp",
                return_value=MagicMock(),
            ),
            patch("aviator.mcp.server.mcp_server.FastMCP", return_value=fake_instance),
        ):
            app = server._create_mcp_app()
            # Simulate AuthMiddleware: set the user in the ContextVar so _list_tools/_call_tool can read it
            from aviator.mcp.server.utils import _mcp_request_user as _user_ctx

            token = _user_ctx.set(user)
            try:
                yield app  # patches remain active here
            finally:
                _user_ctx.reset(token)

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_when_no_restriction(self):
        """None from get_tools_for_tenant means no config in DB → return empty list."""
        user = {"tenantId": "t1", "subscriptionId": "s1"}
        with self._build_app(user, ["tool_a", "tool_b", "tool_c"], allowed=None) as app:
            result = await app._list_tools()
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_list_tools_filters_by_allowed_set(self):
        """Only tools in the allowed set must be returned."""
        user = {"tenantId": "t1", "subscriptionId": "s1"}
        with self._build_app(user, ["tool_a", "tool_b", "tool_c"], allowed={"tool_a", "tool_c"}) as app:
            result = await app._list_tools()
        names = {t.name for t in result}
        assert names == {"tool_a", "tool_c"}
        assert "tool_b" not in names

    @pytest.mark.asyncio
    async def test_list_tools_returns_empty_when_allowed_set_empty(self):
        """An empty allowed set means no tool is accessible."""
        user = {"tenantId": "t1", "subscriptionId": "s1"}
        with self._build_app(user, ["tool_a", "tool_b"], allowed=set()) as app:
            result = await app._list_tools()
        assert result == []


# ---------------------------------------------------------------------------
# call_tool override — permission enforcement & user context
# ---------------------------------------------------------------------------


class TestCallToolOverride:
    """Tests for the call_tool override applied in _create_mcp_app."""

    @contextmanager
    def _build_app(
        self,
        user: dict,
        allowed: set | None,
        call_tool_impl=None,
    ):
        """Context manager — patches stay active so closures resolve correctly."""
        from aviator.mcp.server.mcp_server import AviatorMCPServer

        if call_tool_impl is None:

            async def _default(name, arguments=None, **kwargs):
                return "tool_result"

            call_tool_impl = _default

        _impl = call_tool_impl

        class FakeFastMCP:
            def __init__(self_inner, name, tools):  # noqa: N805
                pass

            def add_middleware(self_inner, middleware):  # noqa: N805
                pass

            async def _list_tools(self_inner):  # noqa: N805
                return []

            async def call_tool(self_inner, name, arguments=None, **kwargs):  # noqa: N805
                return await _impl(name, arguments, **kwargs)

            def run(self_inner, **kwargs):  # noqa: N805
                pass

        fake_instance = FakeFastMCP(name="test", tools=[])

        with patch("aviator.mcp.server.mcp_server.discover_tools", return_value={}):
            server = AviatorMCPServer()

        with (
            patch(
                "aviator.mcp.server.mcp_server.resolve_user_from_request",
                return_value=user,
            ),
            patch(
                "aviator.mcp.server.mcp_server.get_tools_for_tenant",
                return_value=allowed,
            ),
            patch(
                "aviator.mcp.server.mcp_server.langchain_tool_to_mcp",
                return_value=None,
            ),
            patch("aviator.mcp.server.mcp_server.FastMCP", return_value=fake_instance),
        ):
            app = server._create_mcp_app()
            # Simulate AuthMiddleware: set the user in the ContextVar so _list_tools/_call_tool can read it
            from aviator.mcp.server.utils import _mcp_request_user as _user_ctx

            token = _user_ctx.set(user)
            try:
                yield app
            finally:
                _user_ctx.reset(token)

    @pytest.mark.asyncio
    async def test_call_tool_succeeds_when_no_restriction(self):
        user = {"tenantId": "t1", "subscriptionId": "s1"}
        with self._build_app(user=user, allowed={"any_tool"}) as app:
            result = await app.call_tool("any_tool", {})
        assert result == "tool_result"

    @pytest.mark.asyncio
    async def test_call_tool_raises_permission_error_when_not_allowed(self):
        """Calling a tool not in the allowed set must raise HTTPException 403."""
        user = {"tenantId": "t1", "subscriptionId": "s1"}
        with self._build_app(user=user, allowed={"rag_query"}) as app, pytest.raises(HTTPException) as exc_info:
            await app.call_tool("secret_tool", {})
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_call_tool_sets_mcp_user_context(self):
        """call_tool must set _mcp_request_user so get_mcp_user() works inside the tool."""
        from aviator.mcp.server.utils.utils import get_mcp_user

        captured_user: dict = {}

        async def _capture(name, arguments=None, **kwargs):
            captured_user["value"] = get_mcp_user()
            return "ok"

        user = {"tenantId": "t1", "userId": "u99"}
        with self._build_app(user, allowed={"any_tool"}, call_tool_impl=_capture) as app:
            await app.call_tool("any_tool", {})
        assert captured_user["value"] == user

    @pytest.mark.asyncio
    async def test_call_tool_resets_user_context(self):
        """_mcp_request_user must be cleared after call_tool — even when the tool raises."""
        from fastapi import HTTPException

        from aviator.mcp.server.utils.utils import get_mcp_user

        # Normal path
        user = {"tenantId": "t2"}
        with self._build_app(user, allowed={"any_tool"}) as app:
            await app.call_tool("any_tool", {})
        assert get_mcp_user() is None

        # Exception path - actual code catches Exception and raises HTTPException(500)
        async def _boom(name, arguments=None, **kwargs):
            raise RuntimeError("boom")

        with self._build_app({"tenantId": "t3"}, allowed={"any_tool"}, call_tool_impl=_boom) as app:
            with pytest.raises(HTTPException) as exc_info:
                await app.call_tool("any_tool", {})
            assert exc_info.value.status_code == 500
        # User context must be cleared even after exception
        assert get_mcp_user() is None


# ---------------------------------------------------------------------------
# AviatorMCPServer.run
# ---------------------------------------------------------------------------


class TestAviatorMCPServerRun:
    """Tests for run()."""

    def _server(self):
        with patch("aviator.mcp.server.mcp_server.discover_tools", return_value={}):
            from aviator.mcp.server.mcp_server import AviatorMCPServer

            return AviatorMCPServer()

    def test_run_uses_default_port(self, monkeypatch):
        monkeypatch.delenv("MCP_PORT", raising=False)
        server = self._server()

        fake_mcp = MagicMock()
        with patch.object(server, "_create_mcp_app", return_value=fake_mcp):
            server.run()

        fake_mcp.run.assert_called_once_with(host="0.0.0.0", port=8100, transport="http", path="/")  # noqa: S104

    def test_run_uses_env_port_override(self, monkeypatch):
        monkeypatch.setenv("MCP_PORT", "9999")
        server = self._server()

        fake_mcp = MagicMock()
        with patch.object(server, "_create_mcp_app", return_value=fake_mcp):
            server.run()

        fake_mcp.run.assert_called_once_with(host="0.0.0.0", port=9999, transport="http", path="/")  # noqa: S104
