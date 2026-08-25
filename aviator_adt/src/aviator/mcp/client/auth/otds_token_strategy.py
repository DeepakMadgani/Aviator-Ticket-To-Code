"""OTDS token authentication strategy for MCP client."""

from .auth_strategy import AuthStrategy


class OTDSTokenAuthStrategy(AuthStrategy):
    """OTDS token authentication strategy.

    Passes a raw token directly from the request to the MCP server.
    The token comes from Authorization: Bearer <token> header with Bearer prefix removed.
    This strategy adds the Bearer prefix back when sending to MCP server.
    """

    def __init__(self, token: str | None = None, method: str = "header", key_name: str = "Authorization") -> None:
        """Initialize OTDS token auth strategy.

        Args:
            token: Raw token to pass through (Bearer prefix will be added)
            method: How to send the token (only "header" supported)
            key_name: Header name for the token (default: "Authorization")

        """
        self.token = token
        self.method = method
        self.key_name = key_name

    def set_token(self, token: str) -> None:
        """Set the raw token to pass through.

        Token should be the raw value (without Bearer prefix).

        Args:
            token: Raw token extracted from Authorization: Bearer <token> header

        """
        self.token = token

    def get_headers(self) -> dict[str, str]:
        """Return headers with the OTDS token.

        Token is always a raw token (Bearer prefix already removed).
        """
        if not self.token:
            return {}

        if self.method == "header":
            # Token is always raw, so add Bearer prefix
            return {self.key_name: f"Bearer {self.token}"}
        else:
            return {}

    def get_query_params(self) -> dict[str, str]:
        """Return query parameters for authentication.

        OTDS token auth typically uses headers, so return empty dict.
        """
        # OTDS token authentication typically uses headers
        # Could be extended to support query params if needed
        return {}

    def __repr__(self) -> str:
        """Return string representation for debugging."""
        token_preview = self.token[:10] + "..." if self.token and len(self.token) > 10 else self.token
        return f"OTDSTokenAuthStrategy(method={self.method}, key_name={self.key_name}, token={token_preview})"
