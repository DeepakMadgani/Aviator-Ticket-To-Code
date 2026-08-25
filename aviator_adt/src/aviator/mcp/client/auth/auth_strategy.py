"""Base authentication strategy interface for MCP client connections."""

from abc import ABC, abstractmethod


class AuthStrategy(ABC):
    """Base authentication strategy interface. All auth strategies must implement this."""

    @abstractmethod
    def get_headers(self) -> dict[str, str]:
        """Return headers required for authentication.

        Called before each MCP request.
        """

    @abstractmethod
    def get_query_params(self) -> dict[str, str]:
        """Return query parameters required for authentication.

        Called before each MCP request.
        """
