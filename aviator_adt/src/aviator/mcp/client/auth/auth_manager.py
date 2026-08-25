"""Authentication manager factory for MCP client connections."""

from aviator.mcp.models import AuthSchema

from .api_key_strategy import APIKeyAuthStrategy
from .auth_strategy import AuthStrategy
from .oauth_strategy import OAuth2ClientCredentialsStrategy
from .otds_token_strategy import OTDSTokenAuthStrategy


class MCPAuthManager:
    """Factory class to create authentication strategies."""

    @staticmethod
    def create_strategy(auth_schema: AuthSchema) -> AuthStrategy:
        """Create an authentication strategy instance from an AuthSchema."""
        match auth_schema.type:
            case "APIKEY":
                return APIKeyAuthStrategy(
                    apikey=auth_schema.apikey, method=auth_schema.method, key_name=auth_schema.key_name
                )

            case "OAUTH":
                return OAuth2ClientCredentialsStrategy(
                    token_url=auth_schema.token_url,
                    client_id=auth_schema.client_id,
                    client_secret=auth_schema.client_secret,
                    scope=auth_schema.scope,
                    grant_type=auth_schema.grant_type or "client_credentials",
                    username=auth_schema.username,
                    password=auth_schema.password,
                )

            case "OTDS_TOKEN":
                return OTDSTokenAuthStrategy(
                    token=auth_schema.token, method=auth_schema.method, key_name=auth_schema.key_name or "Authorization"
                )

            case _:
                msg = f"Unsupported auth type: {auth_schema.type}"
                raise ValueError(msg)
