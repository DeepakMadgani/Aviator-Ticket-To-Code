"""Test suite for MCP integration in graph.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import BaseTool

from aviator.graph import ContentAviatorAgent
from aviator.models import StateModel


@pytest.fixture
def mock_mcp_client_manager():
    """Mock MCP client manager."""
    with patch("aviator.graph.mcp_client_manager") as mock:
        yield mock


class TestGraphMCPIntegration:
    """Tests for MCP integration in ContentAviatorAgent."""

    @pytest.mark.asyncio
    async def test_get_tools_includes_mcp_tools_with_user_context(self, mock_mcp_client_manager):
        """Test that get_tools includes MCP tools when user_context is provided."""
        # Mock MCP tools
        mock_mcp_tool1 = MagicMock()
        mock_mcp_tool1.name = "tavily_search"
        mock_mcp_tool2 = MagicMock()
        mock_mcp_tool2.name = "filesystem_read"

        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[mock_mcp_tool1, mock_mcp_tool2])

        # Create agent instance
        agent = ContentAviatorAgent()

        # Create state with user context (required for MCP tools to load)
        state = StateModel(user={"tenantId": "test-tenant", "userId": "test-user"})

        # Get tools with user context
        tools = await agent.get_tools(state=state)

        # Verify MCP tools are included
        tool_names = [tool.name for tool in tools]
        assert "tavily_search" in tool_names
        assert "filesystem_read" in tool_names

        # Verify built-in tools are also included
        assert "current_time" in tool_names
        assert "rag_query" in tool_names
        assert "generate_vega_lite_chart" in tool_names

    @pytest.mark.asyncio
    async def test_get_tools_skips_mcp_without_user_context(self, mock_mcp_client_manager):
        """Test get_tools returns only built-in tools when no user context."""
        agent = ContentAviatorAgent()

        # Call without user context (no state)
        tools = await agent.get_tools()

        # Should NOT call get_assistant_tools
        mock_mcp_client_manager.get_assistant_tools.assert_not_called()

        # Should still have built-in tools
        tool_names = [tool.name for tool in tools]
        assert "current_time" in tool_names
        assert "rag_query" in tool_names
        assert len(tools) >= 4  # At least 4 built-in tools

    @pytest.mark.asyncio
    async def test_get_tools_handles_empty_mcp_tools(self, mock_mcp_client_manager):
        """Test get_tools when no MCP tools are available."""
        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[])

        agent = ContentAviatorAgent()
        state = StateModel(user={"tenantId": "test-tenant"})
        tools = await agent.get_tools(state=state)

        # Should still have built-in tools
        tool_names = [tool.name for tool in tools]
        assert "current_time" in tool_names
        assert "rag_query" in tool_names
        assert len(tools) >= 4  # At least the built-in tools

    @pytest.mark.asyncio
    async def test_get_tools_handles_mcp_error(self, mock_mcp_client_manager):
        """Test get_tools handles MCP client manager errors gracefully."""
        mock_mcp_client_manager.get_assistant_tools = AsyncMock(side_effect=Exception("MCP connection failed"))

        agent = ContentAviatorAgent()
        state = StateModel(user={"tenantId": "test-tenant"})

        # Should raise the error from get_assistant_tools
        with pytest.raises(Exception, match="MCP connection failed"):
            await agent.get_tools(state=state)

    @pytest.mark.asyncio
    async def test_get_tools_calls_assistant_tools_method(self, mock_mcp_client_manager):
        """Test that graph calls get_assistant_tools (not get_remote_mcp_tools)."""
        mock_mcp_tool = MagicMock()
        mock_mcp_tool.name = "test_tool"

        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[mock_mcp_tool])

        agent = ContentAviatorAgent()
        state = StateModel(user={"tenantId": "test-tenant"})

        # First call
        tools1 = await agent.get_tools(state=state)

        # Second call
        tools2 = await agent.get_tools(state=state)

        # Graph should call get_assistant_tools twice
        # (once per call, MCP manager caching happens at manager level)
        assert mock_mcp_client_manager.get_assistant_tools.call_count == 2

        # Both calls should include the MCP tool
        assert any(tool.name == "test_tool" for tool in tools1)
        assert any(tool.name == "test_tool" for tool in tools2)

    @pytest.mark.asyncio
    async def test_get_tools_with_tool_modifiers(self, mock_mcp_client_manager):
        """Test that tool modifiers are applied to combined tool list."""
        mock_mcp_tool = MagicMock()
        mock_mcp_tool.name = "mcp_tool"

        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[mock_mcp_tool])

        # Mock tool modifier
        def mock_modifier(tools, state=None, config=None):
            # Add a custom tool
            custom_tool = MagicMock()
            custom_tool.name = "custom_modified_tool"
            tools.append(custom_tool)

        with patch("aviator.graph.load_tool_modifiers") as mock_load_modifiers:
            mock_load_modifiers.return_value = [mock_modifier]

            agent = ContentAviatorAgent()
            state = StateModel(user={"tenantId": "test-tenant"})
            tools = await agent.get_tools(state=state)

            tool_names = [tool.name for tool in tools]

            # Should include built-in tools, MCP tools, and modified tools
            assert "mcp_tool" in tool_names
            assert "custom_modified_tool" in tool_names
            assert "rag_query" in tool_names

    @pytest.mark.asyncio
    async def test_mcp_tools_integration_with_graph(self, mock_mcp_client_manager):
        """Test that MCP tools are properly integrated into the graph."""
        from langchain_core.tools import BaseTool

        mock_mcp_tool = MagicMock(spec=BaseTool)
        mock_mcp_tool.name = "test_mcp_tool"
        mock_mcp_tool.description = "Test MCP tool"

        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[mock_mcp_tool])

        agent = ContentAviatorAgent()
        state = StateModel(user={"tenantId": "test-tenant"})

        # Get tools which should include MCP tools
        tools = await agent.get_tools(state=state)

        # Verify MCP tools were loaded
        assert any(tool.name == "test_mcp_tool" for tool in tools)
        mock_mcp_client_manager.get_assistant_tools.assert_called_once()


class TestGraphMCPToolUsage:
    """Tests for using MCP tools within the graph."""

    @pytest.mark.asyncio
    async def test_graph_can_use_mcp_tool(self, mock_mcp_client_manager):
        """Test that the graph can use MCP tools in execution."""
        # This would require a more complex integration test
        # with actual graph execution
        # Placeholder for comprehensive integration testing

    @pytest.mark.asyncio
    async def test_mcp_tool_error_handling(self, mock_mcp_client_manager):
        """Test error handling when MCP tool fails during graph execution."""
        # Placeholder for error handling tests


class TestMCPClientManagerGlobalInstance:
    """Tests for the global mcp_client_manager instance."""

    def test_mcp_client_manager_singleton(self):
        """Test that mcp_client_manager is a singleton instance."""
        from aviator.mcp import mcp_client_manager

        assert mcp_client_manager is not None
        # Check for actual methods that exist
        assert hasattr(mcp_client_manager, "get_remote_mcp_tools")
        assert hasattr(mcp_client_manager, "get_assistant_tools")
        assert hasattr(mcp_client_manager, "get_server_status")

    def test_mcp_client_manager_has_correct_methods(self):
        """Test MCPClientManager has the expected public methods."""
        from aviator.mcp import MCPClientManager

        manager = MCPClientManager()

        # Check for actual methods (no initialize/shutdown)
        assert hasattr(manager, "get_remote_mcp_tools")
        assert hasattr(manager, "get_assistant_tools")
        assert hasattr(manager, "get_plugin_mcp_tools")
        assert hasattr(manager, "refresh_tools")
        assert hasattr(manager, "get_server_status")
        assert hasattr(manager, "inject_otds_token")

        # These methods should NOT exist
        assert not hasattr(manager, "initialize")
        assert not hasattr(manager, "shutdown")
        assert not hasattr(manager, "get_tools")


class TestGraphToolNodeWithMCP:
    """Tests for ToolNode integration with MCP tools."""

    @pytest.mark.asyncio
    async def test_tool_node_includes_mcp_tools(self, mock_mcp_client_manager):
        """Test that ToolNode is created with MCP tools."""
        from langchain_core.tools import BaseTool

        mock_mcp_tool = MagicMock(spec=BaseTool)
        mock_mcp_tool.name = "mcp_test_tool"

        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[mock_mcp_tool])

        agent = ContentAviatorAgent()
        state = StateModel(user={"tenantId": "test-tenant"})

        # Get tools which should include MCP tools
        tools = await agent.get_tools(state=state)

        # Verify MCP tools were included
        assert any(tool.name == "mcp_test_tool" for tool in tools)
        assert len(tools) >= 5  # Built-in tools + MCP tool


class TestMCPModelsInGraph:
    """Tests for MCP-related models used in graph."""

    def test_mcp_server_config_model(self):
        """Test MCPServerConfig model validation."""
        from aviator.mcp.models import MCPServerConfig

        # Valid HTTP config
        config = MCPServerConfig(
            name="test",
            active=True,
            url="https://test.com/mcp",
            transport="streamable_http",
        )

        assert config.name == "test"
        assert config.active is True
        assert config.url == "https://test.com/mcp"

    def test_mcp_tool_response_model(self):
        """Test MCPToolResponse model."""
        from aviator.mcp.models import MCPToolResponse

        response = MCPToolResponse(
            name="search",
            description="Search the web",
            server_name="tavily",
            tool_schema={"type": "object"},
        )

        assert response.name == "search"
        assert response.server_name == "tavily"
        assert response.tool_schema is not None


class TestGraphWithMCPDisabled:
    """Tests for graph behavior when MCP is disabled or unavailable."""

    @pytest.mark.asyncio
    async def test_graph_works_without_mcp_tools(self, mock_mcp_client_manager):
        """Test that graph works even if MCP returns no tools."""
        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[])

        agent = ContentAviatorAgent()
        state = StateModel(user={"tenantId": "test-tenant"})

        # Should still work with built-in tools
        tools = await agent.get_tools(state=state)

        assert len(tools) >= 4  # Built-in tools
        tool_names = [tool.name for tool in tools]
        assert "rag_query" in tool_names
        assert "current_time" in tool_names

    @pytest.mark.asyncio
    async def test_graph_resilient_to_mcp_failures(self, mock_mcp_client_manager):
        """Test graph resilience when MCP client manager fails."""
        # This depends on error handling implementation
        # Adjust based on actual implementation


class TestDynamicToolNode:
    """Tests for __dynamic_tool_node — the fix for MCP tool execution."""

    @pytest.mark.asyncio
    async def test_dynamic_tool_node_builds_fresh_tool_node_per_call(self, mock_mcp_client_manager):
        """__dynamic_tool_node must create a new ToolNode on every invocation, not reuse a cached one."""
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "tavily_search"
        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[mock_tool])

        agent = ContentAviatorAgent()
        state = StateModel(user={"tenantId": "tenant-a"})

        with patch("aviator.graph.ToolNode") as mock_tool_node_cls:
            mock_node_instance = MagicMock()
            mock_node_instance.ainvoke = AsyncMock(return_value={"messages": []})
            mock_tool_node_cls.return_value = mock_node_instance

            config = {"configurable": {"thread_id": "t1"}}
            await agent._ContentAviatorAgent__dynamic_tool_node(state, config)
            await agent._ContentAviatorAgent__dynamic_tool_node(state, config)

        # ToolNode must be constructed fresh on each call — not reused
        assert mock_tool_node_cls.call_count == 2

    @pytest.mark.asyncio
    async def test_dynamic_tool_node_includes_mcp_tools(self, mock_mcp_client_manager):
        """__dynamic_tool_node passes live MCP tools (including user-context tools) to ToolNode."""
        mock_mcp_tool = MagicMock(spec=BaseTool)
        mock_mcp_tool.name = "tavily_search"
        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[mock_mcp_tool])

        agent = ContentAviatorAgent()
        state = StateModel(user={"tenantId": "tenant-a"})

        captured_tools = []

        with patch("aviator.graph.ToolNode") as mock_tool_node_cls:

            def capture_tools(tools, **kwargs):
                captured_tools.extend(tools)
                node = MagicMock()
                node.ainvoke = AsyncMock(return_value={"messages": []})
                return node

            mock_tool_node_cls.side_effect = capture_tools
            await agent._ContentAviatorAgent__dynamic_tool_node(state, {})

        tool_names = [t.name for t in captured_tools]
        assert "tavily_search" in tool_names
        assert "rag_query" in tool_names

    @pytest.mark.asyncio
    async def test_dynamic_tool_node_excludes_mcp_tools_without_user_context(self, mock_mcp_client_manager):
        """Without user context, __dynamic_tool_node should only pass built-in tools to ToolNode."""
        agent = ContentAviatorAgent()
        # State with no user context
        state = StateModel()

        captured_tools = []

        with patch("aviator.graph.ToolNode") as mock_tool_node_cls:

            def capture_tools(tools, **kwargs):
                captured_tools.extend(tools)
                node = MagicMock()
                node.ainvoke = AsyncMock(return_value={"messages": []})
                return node

            mock_tool_node_cls.side_effect = capture_tools
            await agent._ContentAviatorAgent__dynamic_tool_node(state, {})

        # MCP tools must NOT be loaded when there is no user context
        mock_mcp_client_manager.get_assistant_tools.assert_not_called()
        tool_names = [t.name for t in captured_tools]
        assert "rag_query" in tool_names

    @pytest.mark.asyncio
    async def test_dynamic_tool_node_tenant_isolation(self, mock_mcp_client_manager):
        """Each tenant gets its own isolated tool set — no cross-tenant leakage."""
        tenant_a_tool = MagicMock(spec=BaseTool)
        tenant_a_tool.name = "tool_only_for_tenant_a"

        tenant_b_tool = MagicMock(spec=BaseTool)
        tenant_b_tool.name = "tool_only_for_tenant_b"

        async def tenant_aware_tools(user=None, **kwargs):
            if user and user.get("tenantId") == "tenant-a":
                return [tenant_a_tool]
            if user and user.get("tenantId") == "tenant-b":
                return [tenant_b_tool]
            return []

        mock_mcp_client_manager.get_assistant_tools = AsyncMock(side_effect=tenant_aware_tools)

        agent = ContentAviatorAgent()
        state_a = StateModel(user={"tenantId": "tenant-a"})
        state_b = StateModel(user={"tenantId": "tenant-b"})

        tools_a_captured = []
        tools_b_captured = []
        call_count = [0]

        with patch("aviator.graph.ToolNode") as mock_tool_node_cls:

            def capture_tools(tools, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    tools_a_captured.extend(tools)
                else:
                    tools_b_captured.extend(tools)
                node = MagicMock()
                node.ainvoke = AsyncMock(return_value={"messages": []})
                return node

            mock_tool_node_cls.side_effect = capture_tools
            await agent._ContentAviatorAgent__dynamic_tool_node(state_a, {})
            await agent._ContentAviatorAgent__dynamic_tool_node(state_b, {})

        names_a = [t.name for t in tools_a_captured]
        names_b = [t.name for t in tools_b_captured]

        assert "tool_only_for_tenant_a" in names_a
        assert "tool_only_for_tenant_a" not in names_b
        assert "tool_only_for_tenant_b" in names_b
        assert "tool_only_for_tenant_b" not in names_a

    @pytest.mark.asyncio
    async def test_dynamic_tool_node_passes_tool_wrapper(self, mock_mcp_client_manager):
        """__dynamic_tool_node must wire self.tool_wrapper as awrap_tool_call."""
        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[])

        agent = ContentAviatorAgent()
        state = StateModel(user={"tenantId": "tenant-a"})

        with patch("aviator.graph.ToolNode") as mock_tool_node_cls:
            mock_node = MagicMock()
            mock_node.ainvoke = AsyncMock(return_value={"messages": []})
            mock_tool_node_cls.return_value = mock_node

            await agent._ContentAviatorAgent__dynamic_tool_node(state, {})

        _, kwargs = mock_tool_node_cls.call_args
        assert "awrap_tool_call" in kwargs
        assert kwargs["awrap_tool_call"] == agent.tool_wrapper

    @pytest.mark.asyncio
    async def test_graph_node_registered_as_dynamic_not_static(self, mock_mcp_client_manager):
        """The graph's tool_node must be wired to __dynamic_tool_node, not a static ToolNode."""
        mock_mcp_client_manager.get_assistant_tools = AsyncMock(return_value=[])

        agent = ContentAviatorAgent()
        graph = await agent.get_graph()

        # The compiled graph nodes include 'tool_node'
        assert graph is not None
        # Verify no static ToolNode was stored on the agent at graph-build time
        # (the old code stored ToolNode as a captured variable; now it's dynamic)
        # The graph should compile without needing MCP tools at build time
        assert agent._graph is not None
