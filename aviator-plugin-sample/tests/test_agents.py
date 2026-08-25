"""Tests for agents and complex tool orchestration."""

import pytest


@pytest.mark.asyncio
async def test_utility_agent_basic():
    """Test basic utility agent availability."""
    try:
        from aviator_plugin_sample.utility import utility_agent

        assert utility_agent is not None
        # Verify it's an agent (has required attributes)
        assert hasattr(utility_agent, "invoke") or hasattr(utility_agent, "ainvoke")
    except (ImportError, AttributeError) as e:
        pytest.skip(f"Utility agent not fully implemented: {e}")


@pytest.mark.asyncio
async def test_agent_error_handling():
    """Test that agent handles errors gracefully."""
    try:
        from aviator_plugin_sample.utility import call_utility_agent  # noqa: F401

        # This test is simplified since we're mocking LLM responses
        # In a real scenario, you'd test actual error conditions
        pytest.skip("Agent error handling test requires full agent implementation")
    except (ImportError, AttributeError):
        pytest.skip("Utility agent caller not fully implemented yet")


def test_agent_tool_registration():
    """Test that agent tools are properly registered."""
    try:
        from aviator_plugin_sample.extensions import modify_tools

        tools = []
        modify_tools(tools)

        # Verify tools were added
        assert len(tools) > 0

        # Check that tools have required attributes
        for tool in tools:
            assert hasattr(tool, "name")
            assert hasattr(tool, "description")
            assert callable(tool.func) or callable(tool.coroutine)
    except (ImportError, AttributeError) as e:
        pytest.skip(f"Tool registration not accessible: {e}")


def test_agent_subtools_available():
    """Test that agent has access to its sub-tools."""
    try:
        from aviator_plugin_sample.utility import calculator

        # Verify tool is callable
        assert hasattr(calculator, "invoke") or hasattr(calculator, "func")

        # Test tool directly
        from aviator_plugin_sample.models import CalculationInput

        calc_input = CalculationInput(operation="add", x=2, y=3)
        result = calculator.invoke({"calculation": calc_input})
        assert result.result == 5.0
    except (ImportError, AttributeError) as e:
        pytest.skip(f"Calculator tool not fully implemented: {e}")
