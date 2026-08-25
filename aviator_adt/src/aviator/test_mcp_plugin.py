"""Test MCP plugin for testing MCP integration."""

from aviator.mcp.models import AuthSchema, MCPServerConfig


class TestMCPPlugin:
    """Test plugin that provides MCP server configurations."""

    def get_server_configs(self) -> list[MCPServerConfig]:
        """Return test MCP server configurations."""
        return [
            MCPServerConfig(
                name="tavily",
                enabled=True,
                url="https://mcp.tavily.com/mcp/",
                transport="streamable_http",
                tool_prefix="tavily_",
                tool_scope="default",  # Available to assistant
                auth_schema=AuthSchema(
                    type="APIKEY",
                    method="queryparam",
                    apikey="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",
                    key_name="tavilyApiKey",
                ),
            ),
            MCPServerConfig(
                name="brightdata",
                enabled=True,
                url="https://mcp.brightdata.com/mcp?groups=advanced_scraping",
                transport="streamable_http",
                tool_prefix="brightdata_",
                tool_scope="custom",  # Only for plugin custom nodes
                auth_schema=AuthSchema(
                    type="APIKEY", method="queryparam", apikey="df5846a0-b47a-4e41-8ec4-c509c5e2d7b5", key_name="token"
                ),
            ),
        ]

    def modify_tools(self, tools, server_name):
        """No tool modifications needed for testing."""
        return tools
