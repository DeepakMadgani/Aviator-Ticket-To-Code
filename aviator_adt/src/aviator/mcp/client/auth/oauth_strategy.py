"""OAuth2 authentication strategy for MCP client."""

import time

import requests

from .auth_strategy import AuthStrategy


class OAuth2ClientCredentialsStrategy(AuthStrategy):
    """OAuth2 authentication strategy supporting both client_credentials and password grant types.

    Features:
    - Lazy token fetching
    - Token caching
    - Automatic refresh
    - Supports client_credentials and password grant types
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
        grant_type: str = "client_credentials",
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialize OAuth2ClientCredentialsStrategy with credentials and grant type."""
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.grant_type = grant_type
        self.username = username
        self.password = password

        self._access_token = None
        self._expiry = 0

    def _fetch_token(self) -> None:
        if self.grant_type == "client_credentials":
            payload = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        elif self.grant_type == "password":
            payload = {
                "grant_type": "password",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "username": self.username,
                "password": self.password,
            }
        else:
            msg = f"Unsupported grant type: {self.grant_type}"
            raise ValueError(msg)

        if self.scope:
            payload["scope"] = self.scope

        response = requests.post(self.token_url, data=payload)

        if response.status_code != 200:
            msg = f"Token fetch failed: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        token_data = response.json()

        self._access_token = token_data["access_token"]

        expires_in = token_data.get("expires_in", 3600)

        # refresh slightly before expiry
        self._expiry = time.time() + expires_in - 60

    def _ensure_valid_token(self) -> None:
        if not self._access_token or time.time() >= self._expiry:
            self._fetch_token()

    def get_headers(self) -> dict[str, str]:
        """Return headers with the OAuth2 access token."""
        self._ensure_valid_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def get_query_params(self) -> dict[str, str]:
        """Return query parameters for OAuth2 authentication (empty for this strategy)."""
        return {}
