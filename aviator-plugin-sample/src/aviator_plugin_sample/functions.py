"""Helper functions for custom tools.

This module contains utility functions that can be shared across multiple tools.
Add your own helper functions here as needed.

Example helper functions:
- Data formatters
- API clients
- Validation utilities
- Common calculations
"""

import logging
from typing import Annotated, Any

from aviator.models import StateModel
from langchain.agents import AgentState
from langchain.agents.middleware import after_agent
from langchain.messages import AnyMessage, HumanMessage, ToolMessage
from langchain.tools import ToolException
from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config, get_stream_writer
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import InjectedState
from langgraph.runtime import Runtime
from langgraph.types import Command

logger = logging.getLogger(__name__)


# Add your own helper functions here
@after_agent
def after_agent(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG001
    """Define middleware that extracts artifacts."""

    tool_messages = [msg for msg in state.get("messages", []) if msg.type == "tool"]
    if tool_messages and tool_messages[-1].artifact:
        return tool_messages[-1].artifact
    return None


async def call_agent(
    query: str,
    agent: CompiledStateGraph,
    state: Annotated[StateModel, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    input_messages: list[AnyMessage] | None = None,
) -> Command:
    """Define a tool call that triggers an agent execution."""

    writer = get_stream_writer()
    config = get_config()

    _input = {
        "where": state.where,
        "user": state.user,
        "query": query,
    }

    if input_messages:
        _input["messages"] = input_messages
    else:
        _input["messages"] = [HumanMessage(content=query)]

    try:
        tool_msg = ""
        async for mode, payload in agent.astream(
            _input,
            stream_mode=["values", "messages", "custom"],
            config=config,
        ):
            if mode == "custom":
                writer(payload)
            elif mode == "values":
                result = payload
            elif mode == "messages":
                msg, _metadata = payload
                if isinstance(msg.content, list):
                    content = [item["text"] for item in msg.content if item.get("type") == "text"]
                    content = "".join(content)
                else:
                    content = msg.content

                tool_msg += content

    except ToolException as e:
        logger.error("Error during tool execution -> %s", e)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"Error during tool execution: {e}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    update = {
        "messages": [
            ToolMessage(
                content=result["messages"][-1].content,
                tool_call_id=tool_call_id,
            )
        ],
    }

    # Conditionally add context and references if present in the result
    if "where" in result:
        update["where"] = result["where"]

    if "references" in result:
        update["references"] = result["references"]
        update["show_references"] = False

    return Command(update=update)
