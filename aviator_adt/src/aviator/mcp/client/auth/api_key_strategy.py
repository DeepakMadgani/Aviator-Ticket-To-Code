"""API key authentication strategy for MCP client connections."""

from .auth_strategy import AuthStrategy


class APIKeyAuthStrategy(AuthStrategy):
    """API Key authentication strategy.

    Supports:
    - Header-based API key
    - Query param-based API key
    """

    def __init__(self, apikey: str, method: str, key_name: str = "Authorization") -> None:
        """Initialize APIKeyAuthStrategy with API key, method, and key name."""
        self.apikey = apikey
        self.method = method
        self.key_name = key_name

    def get_headers(self) -> dict[str, str]:
        """Return headers for API key authentication."""
        if self.method == "header":
            return {self.key_name: self.apikey}
        return {}

    def get_query_params(self) -> dict[str, str]:
        """Return query parameters for API key authentication."""
        if self.method == "queryparam":
            return {self.key_name: self.apikey}
        return {}
