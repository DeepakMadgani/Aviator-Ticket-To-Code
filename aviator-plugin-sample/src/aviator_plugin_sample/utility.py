"""Utility agent demonstrating basic agent creation.

This agent shows:
- Creating a utility agent with calculator tools
- Basic tool definitions
- Utility agent invocation
- Minimal error handling
"""

import logging
from typing import Annotated

from aviator.models import StateModel
from aviator.services.llm import LLMRegistry
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from .functions import after_agent, call_agent
from .models import CalculationInput, CalculationResult

logger = logging.getLogger(__name__)


# ============================================================================
# Simple tools for the agent
# ============================================================================


@tool
def calculator(calculation: CalculationInput) -> CalculationResult:
    """Perform basic mathematical operations on two numbers.

    Use this tool when the user wants to perform calculations like addition,
    subtraction, multiplication, or division.

    Args:
        calculation: CalculationInput model containing operation, x, and y values

    Returns:
        CalculationResult with operation details and result

    Raises:
        ToolException: If the operation is unknown

    Example:
        >>> calculator(CalculationInput(operation="add", x=5, y=3))
        CalculationResult(operation="add", input_x=5.0, input_y=3.0, result=8.0, expression="5.0 + 3.0 = 8.0")

    """
    logger.info("Calculator called: %s(%s, %s)", calculation.operation, calculation.x, calculation.y)

    # Define supported operations
    operations = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "divide": lambda a, b: a / b,
    }

    # Perform calculation (validation already handled by Pydantic)
    result = operations[calculation.operation](calculation.x, calculation.y)

    # Format symbols for display
    symbols = {"add": "+", "subtract": "-", "multiply": "x", "divide": "÷"}

    expression = f"{calculation.x} {symbols[calculation.operation]} {calculation.y} = {result}"
    logger.info("Calculation result: %s", expression)

    return CalculationResult(
        operation=calculation.operation,
        input_x=calculation.x,
        input_y=calculation.y,
        result=result,
        expression=expression,
    )


# ============================================================================
# Create the utility agent
# ============================================================================

# Define tools the agent can use

# The agent is created lazily on first use: building it at import time made
# `import aviator_plugin_sample.utility` fail whenever the LLM backend was not
# configured yet (no credentials) or was stubbed by tests.
utility_agent = None


def get_utility_agent():
    global utility_agent
    if utility_agent is None:
        utility_agent = create_agent(
            model=LLMRegistry.get_model(with_provider=True),
            tools=[calculator],
            state_schema=StateModel,
            middleware=[after_agent],
        )
    return utility_agent


# ============================================================================
# Main tool to call the agent
# ============================================================================


@tool(
    description=("An agent that has utility tools like a calculator\n"),
)
async def call_utility_agent(
    query: str,
    state: Annotated[StateModel, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Call utility helper agent for basic tasks.

    Args:
        query (str):
            The user's natural language query.
        state (StateModel):
            The current LangGraph state containing context and user information.
        tool_call_id (str):
            The unique identifier for this tool invocation.

    Returns:
        Command:
         A LangGraph command to update the conversation state with tool results

    """
    logger.info("Utility agent called with: %s", query)

    try:
        # Create messages with custom system prompt
        system_msg = SystemMessage(
            content="You are a helpful assistant. When using the calculator tool, return ONLY the exact 'expression' field from the result. Do not rephrase or reformat it."
        )
        input_messages = [system_msg, HumanMessage(content=query)]

        # Call the utility agent and return using call_agent helper
        return await call_agent(
            query=query,
            agent=get_utility_agent(),
            state=state,
            tool_call_id=tool_call_id,
            input_messages=input_messages,
        )

    except Exception as e:
        logger.error("Error in utility agent: %s", e)
        error_msg = f"Error: {e!s}"
        return (error_msg, {"error": str(e)})
