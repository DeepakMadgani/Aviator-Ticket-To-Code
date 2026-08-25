"""MCP exposure decorator for plugin tools.

Use ``@mcp_expose`` (or ``@mcp_expose(True/False)``) to control whether a
plugin tool is advertised to external MCP clients.  Tools without this
decorator are registered with the Aviator graph normally but are **not**
exposed via the MCP server.

Usage::

    from langchain_core.tools import tool
    from aviator.mcp import mcp_expose

    @mcp_expose          # expose (default True)
    @tool
    async def holidays_canada(province_id: str) -> str:
        ...

    @mcp_expose(False)   # explicitly hide from MCP
    @tool
    async def weather_fetcher(latitude: float, longitude: float) -> str:
        ...

The decorator sets ``_mcp_expose = True | False`` on the ``BaseTool`` object
so that ``aviator.mcp.server.mcp_utils.is_mcptool_exposed`` can detect it
without needing to inspect function signatures.
"""

from collections.abc import Callable
from typing import Any, overload

_MCP_EXPOSE_ATTR = "_mcp_expose"


@overload
def mcp_expose(tool_or_fn: Any) -> Any: ...  # noqa: ANN401


@overload
def mcp_expose(tool_or_fn: bool) -> Callable[[Any], Any]: ...


def mcp_expose(tool_or_fn: Any) -> Any:
    """Mark a tool as exposed (or hidden) via the Aviator MCP server.

    Can be used in three ways:

    * ``@mcp_expose`` — bare decorator, sets ``_mcp_expose = True``.
    * ``@mcp_expose(True)`` — explicit True, same effect.
    * ``@mcp_expose(False)`` — explicit False, hides the tool from MCP clients.

    Args:
        tool_or_fn: When used as a bare decorator this is the ``BaseTool`` or
            callable being decorated.  When called with a boolean this is the
            boolean flag, and the return value is a one-argument decorator.

    Returns:
        The decorated object (bare / ``True`` usage) or a decorator factory
        (``False`` / explicit ``True`` usage).

    Examples::

        @mcp_expose
        @tool
        async def holidays_canada(province_id: str) -> str:
            ...

        @mcp_expose(False)
        @tool
        async def weather_fetcher(latitude: float, longitude: float) -> str:
            ...

    """
    # Called as @mcp_expose(True) or @mcp_expose(False)
    if isinstance(tool_or_fn, bool):
        expose_flag = tool_or_fn

        def _decorator(obj: Any) -> Any:  # noqa: ANN401
            """Set _mcp_expose attribute on the object."""
            return _apply(obj, expose_flag)

        return _decorator

    # Called as bare @mcp_expose
    return _apply(tool_or_fn, flag=True)


def _apply(obj: Any, flag: bool) -> Any:  # noqa: ANN401
    """Set _mcp_expose on obj, using a proxy if the object is immutable."""
    try:
        setattr(obj, _MCP_EXPOSE_ATTR, flag)
    except (AttributeError, TypeError):
        obj = _MCPExposeProxy(obj, flag)
    return obj


class _MCPExposeProxy:
    """Thin proxy that carries ``_mcp_expose`` for immutable objects.

    This is a safety fallback — LangChain ``BaseTool`` instances and plain
    callables both accept attribute assignment, so this class is rarely used.
    """

    def __init__(self, wrapped: Any, flag: bool = True) -> None:  # noqa: ANN401
        self._wrapped = wrapped
        self._mcp_expose: bool = flag

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._wrapped, name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._wrapped(*args, **kwargs)

    def __repr__(self) -> str:
        return f"_MCPExposeProxy({self._wrapped!r}, mcp_expose={self._mcp_expose})"
