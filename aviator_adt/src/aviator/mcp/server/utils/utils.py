"""MCP Server utilities for Aviator ADT."""

import importlib
import inspect
import logging
import pkgutil
import uuid
from collections.abc import Callable
from contextvars import ContextVar

from langchain_core.tools import BaseTool

import aviator.tools
from aviator.database import database_manager
from aviator.database.tenant_lookup_repository import TenantLookupRepository
from aviator.plugins import load_tool_modifiers
from aviator.services.tenant import tenant_id_to_schema_name
from aviator.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP request-scoped user context variable
# ---------------------------------------------------------------------------
# Set once per call_tool invocation in mcp_server.py.
# Any tool can call get_mcp_user() to retrieve the authenticated user without
# needing a `user` parameter in its own signature.

_mcp_request_user: ContextVar[dict | None] = ContextVar("mcp_request_user", default=None)


def get_mcp_user() -> dict | None:
    """Return the authenticated user dict for the current MCP tool call.

    Returns ``None`` when called outside an active MCP request (e.g. in tests
    or direct Python calls).
    """
    return _mcp_request_user.get()


def is_mcptool_exposed(tool: BaseTool) -> bool:
    """Check if a plugin tool should be exposed via the Aviator MCP server.

    Returns ``True`` only when the ``@mcp_expose`` decorator (from
    ``aviator.mcp.decorator``) has been applied with a truthy value.  The
    decorator sets ``_mcp_expose = True | False`` directly on the ``BaseTool``
    object (or on the underlying callable when placed below ``@tool``).

    Tools that lack the decorator entirely are **not** exposed.

    Args:
        tool: The BaseTool to check.

    Returns:
        True if the tool should be exposed via MCP, False otherwise.

    """
    # Check the BaseTool object itself (works when @mcp_expose wraps @tool).
    expose = getattr(tool, "_mcp_expose", None)
    if expose is not None:
        logger.debug("Tool '%s' has _mcp_expose=%s (on BaseTool)", tool.name, expose)
        return bool(expose)

    # Check the underlying callable (works when @mcp_expose is placed below @tool).
    func = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
    if func:
        expose = getattr(func, "_mcp_expose", None)
        if expose is not None:
            logger.debug("Tool '%s' has _mcp_expose=%s (on callable)", tool.name, expose)
            return bool(expose)

    logger.debug("Tool '%s' has no @mcp_expose decorator — skipping", tool.name)
    return False


def discover_tools() -> dict[str, BaseTool]:
    """Discover all available LangChain tools (builtin + plugins).

    Scans ``aviator.tools`` for ``BaseTool`` instances and loads any plugin
    tools registered via the ``aviator.tool_modifiers`` entry point.  Tools
    that require LangGraph-injected parameters (``InjectedState``,
    ``InjectedToolCallId``) are excluded because they cannot be invoked
    standalone by MCP clients.

    Returns:
        Dict of ``{tool_name: BaseTool}`` ready to be passed to
        :func:`langchain_tool_to_mcp`.

    """
    tools_map: dict[str, BaseTool] = {}

    # 1. Built-in tools from aviator.tools package
    for _, module_name, _ in pkgutil.iter_modules(aviator.tools.__path__):
        try:
            module = importlib.import_module(f"aviator.tools.{module_name}")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, BaseTool):
                    if is_mcptool_exposed(attr):
                        tools_map[attr.name] = attr
                        logger.debug(
                            "Built-in ADT tool '%s' has mcp_expose=True — will be exposed",
                            attr.name,
                        )
                    else:
                        logger.debug(
                            "Skipping built-in ADT tool '%s' (mcp_expose not set or False)",
                            attr.name,
                        )
        except Exception:
            logger.exception("Error loading tools from aviator.tools.%s", module_name)

    # 2. Plugin tools via tool_modifiers entry point
    plugin_tools: list = []
    for modify in load_tool_modifiers():
        try:
            modify(tools=plugin_tools, state=None, config=None)
            logger.info("Loaded tool modifier: %s", modify.__name__)
        except Exception:
            logger.exception("Error running tool modifier %s", modify)

    for tool in plugin_tools:
        if isinstance(tool, BaseTool):
            if is_mcptool_exposed(tool):
                tools_map[tool.name] = tool
                logger.debug(
                    "Plugin tool '%s' has mcp_expose=True — will be exposed",
                    tool.name,
                )
            else:
                logger.debug(
                    "Skipping plugin tool '%s' (mcp_expose not set or False)",
                    tool.name,
                )

    # 3. For tools with LangGraph-injected params(like state): wrap (if mcp_expose) or skip.
    #    Wrapping fills InjectedState from the current MCP request context so the
    #    original tool logic runs without needing a live graph.
    filtered: dict[str, BaseTool] = {}
    for name, tool in tools_map.items():
        fn = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
        if fn:
            try:
                injected = [p for p in inspect.signature(fn).parameters.values() if "Injected" in str(p.annotation)]
                if injected:
                    wrapped = create_mcp_wrapper(tool)
                    if wrapped:
                        filtered[name] = wrapped
                        logger.info(
                            "Wrapped tool '%s' for MCP (stripped injected params: %s)",
                            name,
                            [p.name for p in injected],
                        )
                    else:
                        logger.warning("Could not create MCP wrapper for tool '%s', skipping", name)
                    continue  # do NOT fall through to filtered[name] = tool below

            except Exception:
                logger.debug("Could not inspect signature for tool '%s'—skipping injection check", name)
        filtered[name] = tool

    logger.info("Discovered %d tools (%d total before filter)", len(filtered), len(tools_map))
    return filtered


def create_mcp_wrapper(tool: BaseTool) -> BaseTool | None:
    """Create an MCP-safe wrapper for a graph tool that has LangGraph-injected parameters.

    When a tool uses ``InjectedState`` (or similar), MCP clients cannot supply those
    parameters — the LangGraph runtime normally injects them.  This wrapper:

    1. Strips all ``InjectedState`` parameters and the ``mcp_expose`` marker from the
       exposed function signature so MCP clients only see (and must provide) the real
       business parameters.
    2. At call-time, synthesises the missing ``StateModel`` from the current MCP request
       context via :func:`get_mcp_user`, so the original tool logic runs unchanged.

    This is the single place that handles InjectedState for MCP — no duplicate tool
    implementation required.  Any future graph tool with injected params can be exposed
    to MCP simply by marking it with ``tool.metadata = {"mcp_expose": True}``.

    Args:
        tool: A ``BaseTool`` with at least one ``InjectedState`` parameter.

    Returns:
        A new ``@tool``-decorated wrapper with injected params stripped, or ``None``
        if the tool has no ``InjectedState`` params or its callable cannot be extracted.

    """
    from functools import wraps

    from langchain_core.tools import tool as lc_tool

    from aviator.models import StateModel

    fn = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
    if not fn:
        logger.warning("create_mcp_wrapper: tool '%s' has no callable", tool.name)
        return None

    sig = inspect.signature(fn)

    # Find LangGraph-injected params — filled by the runtime, not by MCP callers.
    # InjectedState: synthesised from the authenticated MCP user.
    # InjectedToolCallId: generated as a fresh UUID for each MCP invocation.
    state_injected_names = [name for name, param in sig.parameters.items() if "InjectedState" in str(param.annotation)]
    tool_call_id_names = [
        name for name, param in sig.parameters.items() if "InjectedToolCallId" in str(param.annotation)
    ]
    all_injected_names = state_injected_names + tool_call_id_names
    if not all_injected_names:
        return None  # nothing to wrap

    # Build a clean signature: drop all injected params and the mcp_expose marker
    # so MCP clients only see (and must supply) the real business parameters.
    strip_params = set(all_injected_names) | {"mcp_expose"}
    clean_params = [p for name, p in sig.parameters.items() if name not in strip_params]
    clean_sig = sig.replace(parameters=clean_params)

    @wraps(fn)
    async def _wrapper(**kwargs: object) -> object:
        from langchain_core.runnables.config import var_child_runnable_config
        from langgraph._internal._constants import CONFIG_KEY_RUNTIME
        from langgraph.runtime import DEFAULT_RUNTIME

        user = get_mcp_user()
        # Synthesise StateModel from the authenticated MCP user.
        state = StateModel(user=user or {})
        for name in state_injected_names:
            kwargs[name] = state
        # Generate a unique tool_call_id for each MCP invocation.
        for name in tool_call_id_names:
            kwargs[name] = uuid.uuid4().hex

        # LangGraph tools that call get_stream_writer() / get_config() require
        # a runnable context with __pregel_runtime set.  We inject DEFAULT_RUNTIME
        # which has a no-op stream_writer, so streaming calls are silently dropped
        # (MCP clients receive the tool's return value directly).
        token = var_child_runnable_config.set({"configurable": {CONFIG_KEY_RUNTIME: DEFAULT_RUNTIME}})
        try:
            return await fn(**kwargs)
        finally:
            var_child_runnable_config.reset(token)

    # Override the signature so FastMCP (and any JSON-schema introspection) only
    # sees the real business parameters — not the injected ones.
    _wrapper.__signature__ = clean_sig  # type: ignore[attr-defined]
    # Use tool.name (not fn.__name__) so FastMCP registers the tool under the correct
    # MCP name even when @tool("custom_name") differs from the function name.
    _wrapper.__name__ = tool.name
    _wrapper.__doc__ = fn.__doc__

    wrapped = lc_tool(_wrapper)
    logger.debug(
        "create_mcp_wrapper: wrapped tool '%s' — stripped params: %s",
        tool.name,
        all_injected_names,
    )
    return wrapped


def langchain_tool_to_mcp(tool: BaseTool) -> Callable | None:
    """Extract the callable from a LangChain ``BaseTool`` for FastMCP registration.

    For ``@tool``-decorated functions, LangChain sets ``tool.name`` from either
    the function name or an explicit name argument (e.g. ``@tool("custom_name")``).
    FastMCP derives the MCP tool name from the callable's ``__name__``, so this
    function aligns ``fn.__name__`` with ``tool.name`` when they differ to ensure
    the correct name is registered.

    Args:
        tool: LangChain ``BaseTool`` instance.

    Returns:
        The underlying callable (coroutine or sync func), or ``None`` if none
        could be extracted.

    """
    fn = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
    if not callable(fn):
        logger.warning("Cannot convert tool '%s': no callable found", tool.name)
        return None
    # FastMCP derives the MCP tool name from the callable's __name__.
    # When @tool("custom_name") is used, tool.name differs from fn.__name__;
    # align them here so the registered MCP tool name is always tool.name.
    if fn.__name__ != tool.name:
        logger.debug(
            "Tool '%s': __name__ mismatch (fn='%s') — aligning for FastMCP",
            tool.name,
            fn.__name__,
        )
        fn.__name__ = tool.name
    logger.debug("Extracted callable from LangChain tool '%s'", tool.name)
    return fn


def get_tools_for_tenant(tenant_id: str | None, subscription_id: str | None = None) -> set[str] | None:
    """Return the set of tool names permitted for a given tenant.

    Looks up rows in ``tenant_<tenant_id>.tenant_lookup_config`` where
    ``key = 'ALLOWED_MCP_TOOLS'`` and ``subscription_id`` matches.
    The ``value`` column is a comma-separated list of tool names.

    Args:
        tenant_id: Tenant identifier (may be ``None``).
        subscription_id: Subscription identifier (may be ``None``).

    Returns:
        Set of permitted tool name strings, or ``None`` meaning no
        restriction (all tools allowed).  The caller intersects this
        with whatever tools are actually registered in FastMCP.

    """

    import json

    try:
        schema_name = tenant_id_to_schema_name(tenant_id)
        with database_manager.session(schema_name) as db:
            rows = TenantLookupRepository(db).get(subscription_id=subscription_id, key=settings.mcp_tools_config_key)
            allowed: set[str] = set()
            for row in rows:
                if row.key == settings.mcp_tools_config_key and row.value:
                    try:
                        tool_list = json.loads(row.value)
                        allowed.update(tool["name"] for tool in tool_list if isinstance(tool, dict) and "name" in tool)
                    except Exception as e:
                        logger.warning("Failed to parse tool config JSON for tenant '%s': %s", tenant_id, e)
            if allowed:
                logger.debug("Tenant '%s' allowed MCP tools: %s", tenant_id, allowed)
                return allowed
    except Exception:
        logger.exception("Failed to fetch tool config for tenant '%s', allowing all tools", tenant_id)

    return None  # fallback: allow all
