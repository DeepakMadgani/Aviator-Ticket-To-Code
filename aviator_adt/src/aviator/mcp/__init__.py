"""MCP subsystem for Aviator ADT."""

from .client.manager import MCPClientManager
from .models import (
    AuthSchema,
    MCPServerConfig,
    MCPServerConfigResponse,
    MCPToolResponse,
)

# Singleton pattern following ADT conventions
_mcp_client_manager: MCPClientManager | None = None


def get_mcp_client_manager() -> MCPClientManager:
    """Get or create the MCP client manager singleton."""
    global _mcp_client_manager  # noqa: PLW0603
    if _mcp_client_manager is None:
        _mcp_client_manager = MCPClientManager()
    return _mcp_client_manager


def __getattr__(name: str) -> object:
    """Lazy import for mcp_expose to avoid circular imports.

    mcp_expose → aviator.api.auth → aviator.api.__init__ → aviator.graph
    → aviator.mcp (circular). Deferring this import breaks the cycle.
    """
    if name == "mcp_expose":
        from .server.utils.decorator import mcp_expose

        return mcp_expose
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


__all__ = [
    "AuthSchema",
    "MCPClientManager",
    "MCPServerConfig",
    "MCPServerConfigResponse",
    "MCPToolResponse",
    "get_mcp_client_manager",
    "mcp_client_manager",
    "mcp_expose",
]

# Export for backward compatibility
mcp_client_manager = get_mcp_client_manager()
