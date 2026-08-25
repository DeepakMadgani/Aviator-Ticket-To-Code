"""Unit tests for aviator.mcp.server.utils.utils."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import BaseTool, tool

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_tool(name: str = "my_tool", expose: bool | None = None) -> BaseTool:
    """Return a simple @tool-decorated function, optionally with _mcp_expose set."""

    @tool
    def my_func(query: str) -> str:
        """Simulate a test tool."""
        return f"result:{query}"

    my_func.name = name
    if expose is not None:
        my_func._mcp_expose = expose  # type: ignore[attr-defined]
    return my_func


def _make_tool_with_exposed_callable(name: str = "callable_tool", expose: bool = True) -> BaseTool:
    """Return a tool whose underlying *callable* carries _mcp_expose (not the BaseTool)."""

    @tool
    def my_func(query: str) -> str:
        """Simulate a test tool with expose on callable."""
        return query

    my_func.name = name
    # Simulate @mcp_expose placed *below* @tool — set on the raw callable.
    fn = getattr(my_func, "coroutine", None) or getattr(my_func, "func", None)
    if fn:
        fn._mcp_expose = expose  # type: ignore[attr-defined]
    return my_func


# ---------------------------------------------------------------------------
# Tests: get_mcp_user / _mcp_request_user
# ---------------------------------------------------------------------------


class TestGetMcpUser:
    """Tests for get_mcp_user and _mcp_request_user ContextVar."""

    def test_returns_none_by_default(self):
        """Outside an MCP request the user must be None."""
        from aviator.mcp.server.utils.utils import get_mcp_user

        assert get_mcp_user() is None

    def test_returns_user_after_contextvar_set(self):
        """Setting the ContextVar returns that value; resetting restores None."""
        from aviator.mcp.server.utils.utils import _mcp_request_user, get_mcp_user

        user = {"tenantId": "t1", "userId": "u1"}
        token = _mcp_request_user.set(user)
        try:
            assert get_mcp_user() == user
        finally:
            _mcp_request_user.reset(token)

        assert get_mcp_user() is None


# ---------------------------------------------------------------------------
# Tests: is_mcptool_exposed
# ---------------------------------------------------------------------------


class TestIsMcptoolExposed:
    """Tests for is_mcptool_exposed."""

    def test_returns_true_when_attribute_true_on_base_tool(self):
        from aviator.mcp.server.utils.utils import is_mcptool_exposed

        tool_obj = _make_tool(expose=True)
        assert is_mcptool_exposed(tool_obj) is True

    def test_returns_false_when_not_exposed(self):
        """False when explicitly False and when the attribute is absent entirely."""
        from aviator.mcp.server.utils.utils import is_mcptool_exposed

        assert is_mcptool_exposed(_make_tool(expose=False)) is False
        assert is_mcptool_exposed(_make_tool(expose=None)) is False

    def test_returns_true_when_attribute_on_callable(self):
        """_mcp_expose on the underlying callable (below @tool) should be detected."""
        from aviator.mcp.server.utils.utils import is_mcptool_exposed

        tool_obj = _make_tool_with_exposed_callable(expose=True)
        assert is_mcptool_exposed(tool_obj) is True

    def test_base_tool_attribute_takes_precedence_over_callable(self):
        """If both BaseTool and callable carry the flag, BaseTool wins."""
        from aviator.mcp.server.utils.utils import is_mcptool_exposed

        tool_obj = _make_tool(expose=False)
        fn = getattr(tool_obj, "coroutine", None) or getattr(tool_obj, "func", None)
        if fn:
            fn._mcp_expose = True  # callable says True …
        # … but BaseTool says False → BaseTool wins
        assert is_mcptool_exposed(tool_obj) is False


# ---------------------------------------------------------------------------
# Tests: langchain_tool_to_mcp
# ---------------------------------------------------------------------------


class TestLangchainToolToMcp:
    """Tests for langchain_tool_to_mcp."""

    def test_extracts_callable_from_tool(self):
        """Returns a callable for both async and sync @tool functions."""
        from aviator.mcp.server.utils.utils import langchain_tool_to_mcp

        @tool
        async def async_tool(x: str) -> str:
            """Async tool."""
            return x

        @tool
        def sync_tool(x: str) -> str:
            """Sync tool."""
            return x

        assert callable(langchain_tool_to_mcp(async_tool))
        assert callable(langchain_tool_to_mcp(sync_tool))

    def test_returns_none_for_tool_without_callable(self):
        from aviator.mcp.server.utils.utils import langchain_tool_to_mcp

        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "no_callable"
        # Neither coroutine nor func attribute
        del mock_tool.coroutine
        del mock_tool.func
        mock_tool.coroutine = None
        mock_tool.func = None

        result = langchain_tool_to_mcp(mock_tool)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: get_tools_for_tenant
# ---------------------------------------------------------------------------


class TestGetToolsForTenant:
    """Tests for get_tools_for_tenant."""

    def test_returns_none_when_tenant_id_is_none(self):
        from aviator.mcp.server.utils.utils import get_tools_for_tenant

        assert get_tools_for_tenant(None, "sub-1") is None

    def test_returns_none_when_subscription_id_is_none(self):
        from aviator.mcp.server.utils.utils import get_tools_for_tenant

        assert get_tools_for_tenant("tenant-1", None) is None

    def test_returns_tool_set_from_db(self):
        """Only rows matching the config key are included; wrong-key rows are ignored."""
        from contextlib import contextmanager

        from aviator.mcp.server.utils.utils import get_tools_for_tenant

        rows = [
            SimpleNamespace(key="OTHER_KEY", value='[{"name": "secret_tool"}]'),
            SimpleNamespace(key="ALLOWED_MCP_TOOLS", value='[{"name": "rag_query"}, {"name": "current_time"}]'),
        ]
        mock_db = MagicMock()
        mock_manager = MagicMock()

        @contextmanager
        def fake_session(*a, **kw):
            yield mock_db

        mock_manager.session = fake_session

        with (
            patch("aviator.mcp.server.utils.utils.database_manager", mock_manager),
            patch("aviator.mcp.server.utils.utils.TenantLookupRepository") as mock_repo,
        ):
            mock_repo.return_value.get.return_value = rows
            result = get_tools_for_tenant("tenant-1", "sub-1")

        assert result == {"rag_query", "current_time"}
        assert "secret_tool" not in result

    def test_returns_none_on_db_exception(self):
        """DB errors must not propagate — fallback to allow-all."""
        from contextlib import contextmanager

        from aviator.mcp.server.utils.utils import get_tools_for_tenant

        mock_manager = MagicMock()

        @contextmanager
        def failing_session(*a, **kw):
            raise RuntimeError("DB down")
            yield  # required by @contextmanager

        mock_manager.session = failing_session

        with patch("aviator.mcp.server.utils.utils.database_manager", mock_manager):
            result = get_tools_for_tenant("tenant-1", "sub-1")

        assert result is None

    def test_ignores_empty_value_in_row(self):
        from contextlib import contextmanager

        from aviator.mcp.server.utils.utils import get_tools_for_tenant

        rows = [SimpleNamespace(key="ALLOWED_MCP_TOOLS", value="")]
        mock_db = MagicMock()
        mock_manager = MagicMock()

        @contextmanager
        def fake_session(*a, **kw):
            yield mock_db

        mock_manager.session = fake_session

        with (
            patch("aviator.mcp.server.utils.utils.database_manager", mock_manager),
            patch("aviator.mcp.server.utils.utils.TenantLookupRepository") as mock_repo,
        ):
            mock_repo.return_value.get.return_value = rows
            result = get_tools_for_tenant("tenant-1", "sub-1")

        # Empty value → no tools collected → fallback None
        assert result is None

    def test_multiple_rows_merged(self):
        """Multiple rows for the same key should have their tools merged."""
        from contextlib import contextmanager

        from aviator.mcp.server.utils.utils import get_tools_for_tenant

        rows = [
            SimpleNamespace(key="ALLOWED_MCP_TOOLS", value='[{"name": "rag_query"}]'),
            SimpleNamespace(key="ALLOWED_MCP_TOOLS", value='[{"name": "current_time"}]'),
        ]
        mock_db = MagicMock()
        mock_manager = MagicMock()

        @contextmanager
        def fake_session(*a, **kw):
            yield mock_db

        mock_manager.session = fake_session

        with (
            patch("aviator.mcp.server.utils.utils.database_manager", mock_manager),
            patch("aviator.mcp.server.utils.utils.TenantLookupRepository") as mock_repo,
        ):
            mock_repo.return_value.get.return_value = rows
            result = get_tools_for_tenant("tenant-1", "sub-1")

        assert result == {"rag_query", "current_time"}


# ---------------------------------------------------------------------------
# Tests: discover_tools
# ---------------------------------------------------------------------------


class TestDiscoverTools:
    """Tests for discover_tools.

    Uses patch.object on the module's own pkgutil/importlib references rather
    than dotted-string patches, to avoid replacing importlib.import_module
    globally (which breaks Python's own mock infrastructure).
    """

    def _run_discovery(self, iter_result, import_result=None, import_side_effect=None) -> dict:
        """Patch pkgutil/importlib on the utils module and run discover_tools."""
        import aviator.mcp.server.utils.utils as _m

        mock_pkgutil = MagicMock()
        mock_pkgutil.iter_modules.return_value = iter_result

        mock_importlib = MagicMock()
        if import_side_effect is not None:
            mock_importlib.import_module.side_effect = import_side_effect
        elif import_result is not None:
            mock_importlib.import_module.return_value = import_result

        with (
            patch.object(_m, "pkgutil", mock_pkgutil),
            patch.object(_m, "importlib", mock_importlib),
            patch.object(_m, "load_tool_modifiers", return_value=[]),
        ):
            return _m.discover_tools()

    def test_exposed_builtin_tool_is_included(self):
        """A built-in tool with _mcp_expose=True must appear in the result."""
        import types

        exposed = _make_tool(name="exposed_tool", expose=True)
        fake_module = types.SimpleNamespace(exposed_tool=exposed)
        result = self._run_discovery([(None, "fake_mod", False)], import_result=fake_module)
        assert "exposed_tool" in result

    def test_unexposed_builtin_tool_is_excluded(self):
        """A built-in tool without _mcp_expose must not appear in the result."""
        import types

        hidden = _make_tool(name="hidden_tool", expose=None)
        fake_module = types.SimpleNamespace(hidden_tool=hidden)
        result = self._run_discovery([(None, "fake_mod", False)], import_result=fake_module)
        assert "hidden_tool" not in result

    def test_broken_module_does_not_crash_discovery(self):
        """An ImportError is caught; discovery returns an empty dict (not a crash)."""
        result = self._run_discovery([(None, "bad_mod", False)], import_side_effect=ImportError("broken"))
        assert result == {}

    def test_exposed_plugin_tool_is_included(self):
        """Plugin tools with _mcp_expose=True must be included."""
        plugin_tool = _make_tool(name="plugin_tool", expose=True)

        def modifier(tools, state=None, config=None, **kwargs):
            tools.append(plugin_tool)

        with (
            patch("aviator.mcp.server.utils.utils.pkgutil.iter_modules", return_value=[]),
            patch("aviator.mcp.server.utils.utils.load_tool_modifiers", return_value=[modifier]),
        ):
            from aviator.mcp.server.utils.utils import discover_tools

            result = discover_tools()

        assert "plugin_tool" in result

    def test_unexposed_plugin_tool_is_excluded(self):
        """Plugin tools without _mcp_expose must not surface."""
        plugin_tool = _make_tool(name="private_plugin", expose=None)

        def modifier(tools, state=None, config=None, **kwargs):
            tools.append(plugin_tool)

        with (
            patch("aviator.mcp.server.utils.utils.pkgutil.iter_modules", return_value=[]),
            patch("aviator.mcp.server.utils.utils.load_tool_modifiers", return_value=[modifier]),
        ):
            from aviator.mcp.server.utils.utils import discover_tools

            result = discover_tools()

        assert "private_plugin" not in result

    def test_failing_plugin_modifier_does_not_crash(self):
        """A plugin modifier that raises must be swallowed and discovery continues."""

        def bad_modifier(tools, state=None, config=None, **kwargs):
            raise RuntimeError("plugin bug")

        with (
            patch("aviator.mcp.server.utils.utils.pkgutil.iter_modules", return_value=[]),
            patch(
                "aviator.mcp.server.utils.utils.load_tool_modifiers",
                return_value=[bad_modifier],
            ),
        ):
            from aviator.mcp.server.utils.utils import discover_tools

            result = discover_tools()

        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Tests: create_mcp_wrapper
# ---------------------------------------------------------------------------


class TestCreateMcpWrapper:
    """Tests for create_mcp_wrapper."""

    def test_returns_none_for_tool_without_injected_params(self):
        from aviator.mcp.server.utils.utils import create_mcp_wrapper

        plain = _make_tool(name="plain_tool")
        assert create_mcp_wrapper(plain) is None

    def test_returns_none_for_tool_without_callable(self):
        from aviator.mcp.server.utils.utils import create_mcp_wrapper

        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "no_fn"
        mock_tool.coroutine = None
        mock_tool.func = None

        assert create_mcp_wrapper(mock_tool) is None

    def test_wrapper_signature_strips_injected_and_keeps_business_params(self):
        """Injected params must be stripped; all business params must survive."""
        from typing import Annotated

        from langgraph.prebuilt import InjectedState

        from aviator.mcp.server.utils.utils import create_mcp_wrapper

        @tool
        async def rich_tool(
            query: str,
            num_results: int,
            state: Annotated[dict, InjectedState],
        ) -> str:
            """Tool with both business and injected params."""
            return query

        wrapped = create_mcp_wrapper(rich_tool)
        assert wrapped is not None

        fn = getattr(wrapped, "coroutine", None) or getattr(wrapped, "func", None)
        assert fn is not None
        sig = inspect.signature(fn)
        assert "query" in sig.parameters
        assert "num_results" in sig.parameters
        assert "state" not in sig.parameters

    @pytest.mark.asyncio
    async def test_wrapper_injects_state_at_call_time(self):
        """Wrapper injects StateModel from MCP user context; falls back to empty user."""
        from typing import Annotated

        from langgraph.prebuilt import InjectedState

        from aviator.mcp.server.utils.utils import _mcp_request_user, create_mcp_wrapper

        captured: dict = {}

        @tool
        async def stateful_tool(
            query: str,
            state: Annotated[dict, InjectedState],
        ) -> str:
            """Capture the current MCP user into state."""
            captured["user"] = getattr(state, "user", None)
            return "ok"

        wrapped = create_mcp_wrapper(stateful_tool)
        assert wrapped is not None
        fn = getattr(wrapped, "coroutine", None) or getattr(wrapped, "func", None)

        # With an active MCP user — state must reflect it.
        user = {"tenantId": "t1"}
        token = _mcp_request_user.set(user)
        try:
            await fn(query="hello")
        finally:
            _mcp_request_user.reset(token)
        assert captured["user"] == user

        # Without an active MCP user — state.user must be an empty dict.
        await fn(query="fallback")
        assert captured["user"] == {}
