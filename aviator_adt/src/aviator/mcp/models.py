"""MCP-specific Pydantic models."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AuthType = Literal["APIKEY", "OAUTH", "OTDS_TOKEN"]
AuthMethod = Literal["header", "queryparam", "payload"]
OAuthGrantType = Literal["client_credentials", "password"]


class AuthSchema(BaseModel):
    """Authentication schema for MCP server."""

    type: AuthType
    method: AuthMethod

    # API Key fields
    apikey: str | None = None
    key_name: str | None = "Authorization"

    # OAuth fields
    token_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None
    grant_type: OAuthGrantType | None = "client_credentials"
    username: str | None = None
    password: str | None = None

    # OTDS token fields
    token: str | None = None  # Token to pass through from request

    @model_validator(mode="after")
    def validate_auth(self) -> "AuthSchema":
        """Validate authentication configuration for the MCP server."""
        if self.type == "APIKEY":
            if not self.apikey:
                msg = "apikey required for APIKEY auth"
                raise ValueError(msg)

            if self.method not in ["header", "queryparam"]:
                msg = "APIKEY method must be header or queryparam"
                raise ValueError(msg)

        elif self.type == "OTDS_TOKEN":
            # No required fields for OTDS token - token will be injected at runtime
            if self.method != "header":
                msg = "OTDS_TOKEN method must be header"
                raise ValueError(msg)

        elif self.type == "OAUTH":
            if not self.client_id or not self.client_secret or not self.token_url:
                msg = "client_id, client_secret, token_url required for OAUTH"
                raise ValueError(msg)

            if self.method != "payload":
                msg = "OAuth credentials must be sent in payload"
                raise ValueError(msg)

            # Validate grant type specific fields
            if self.grant_type == "password" and (not self.username or not self.password):
                msg = "username and password required for password grant type"
                raise ValueError(msg)

        return self


TransportType = Literal["streamable_http", "sse", "stdio"]


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server (request model)."""

    name: str = Field(..., description="Unique identifier for the server")
    active: bool = Field(default=True, description="Whether the server is enabled")

    # HTTP Transport
    url: str | None = Field(None, description="URL for HTTP-based servers")
    transport: TransportType = Field(
        default="streamable_http", description="Transport type: 'streamable_http', 'sse', or 'stdio'"
    )

    # STDIO Transport
    command: str | None = Field(None, description="Command for STDIO servers")
    args: list[str] = Field(default_factory=list, description="Arguments for STDIO command")

    # Tool configuration
    tool_scope: Literal["default", "custom"] = Field(
        default="default",
        description="Where tools should be available: default (assistant graph), custom (plugin nodes)",
    )

    # Authentication configuration
    auth_schema: AuthSchema | None = Field(None, description="Authentication configuration for the server")

    class Config:
        """Pydantic configuration for MCPServerConfig model."""

        extra = "forbid"

    @model_validator(mode="after")
    def validate_transport_requirements(self) -> "MCPServerConfig":
        """Validate that required fields are present for the selected transport."""
        if self.transport in ("streamable_http", "sse"):
            if not self.url:
                msg = f"'url' is required for transport '{self.transport}'"
                raise ValueError(msg)
        elif self.transport == "stdio" and not self.command:
            msg = "'command' is required for transport 'stdio'"
            raise ValueError(msg)
        return self


class MCPServerConfigResponse(BaseModel):
    """Response model for MCP server configuration (includes server id)."""

    id: str = Field(..., description="Unique server identifier (UUID)")
    name: str = Field(..., description="Server name")
    active: bool = Field(..., description="Whether the server is enabled")

    # HTTP Transport
    url: str | None = Field(None, description="URL for HTTP-based servers")
    transport: TransportType = Field(default="streamable_http", description="Transport type")

    # STDIO Transport
    command: str | None = Field(None, description="Command for STDIO servers")
    args: list[str] = Field(default_factory=list, description="Arguments for STDIO command")

    # Tool configuration
    tool_scope: Literal["default", "custom"] = Field(
        default="default",
        description="Where tools should be available: default (assistant graph), custom (plugin nodes)",
    )

    # Authentication configuration
    auth_schema: AuthSchema | None = Field(None, description="Authentication configuration for the server")


class MCPToolResponse(BaseModel):
    """Response model for MCP tools."""

    name: str
    description: str | None = None
    server_name: str | None = None
    tool_schema: dict[str, Any] | None = None


#
# Tenant Lookup Config Models
#


class TenantLookupConfigCreateRequest(BaseModel):
    """Request model for creating/upserting a tenant_lookup_config entry.

    Represents a row in tenant_<tenant_id>.tenant_lookup_config table.

    Schema: tenant_<tenant_id>
    Table: tenant_lookup_config (id, key, value, subscription_id)
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "tools": ["tool_a", "tool_b"],
                }
            ]
        },
    )

    tools: list[str] = Field(..., description="List of tools to register for the tenant and subscription")


class TenantLookupConfigResponse(BaseModel):
    """Response model for a tenant_lookup_config entry.

    Represents a row in tenant_lookup_config table (stored in public or tenant schema).

    Table: tenant_lookup_config (id, key, value, subscription_id)
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "key": "ALLOWED_MCP_TOOLS",
                    "value": [
                        {"name": "tool_a", "description": "Description of tool_a"},
                        {"name": "tool_b", "description": "Description of tool_b"},
                    ],
                    "subscription_id": "sub-001",
                }
            ]
        },
    )

    id: int = Field(..., description="Auto-generated primary key")
    key: str = Field(..., description="Configuration key name", min_length=1)
    value: list[MCPToolResponse] = Field(
        ..., description="Configuration value as a list of allowed tools (name + description)"
    )
    subscription_id: str | None = Field(None, description="Subscription this config entry belongs to")
