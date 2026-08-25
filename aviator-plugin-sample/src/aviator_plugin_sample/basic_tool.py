"""Basic tool example - Simple greeting tool."""

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def greeting_tool(name: str) -> str:
    """Generate a friendly greeting for the given name.

    Use this tool when the user wants to greet someone or say hello.

    Args:
        name: The name of the person to greet

    Returns:
        A personalized greeting message

    Example:
        >>> greeting_tool("Alice")
        "Hello, Alice! Welcome to Aviator!"

    """
    logger.info("Greeting tool called for: %s", name)

    greeting = f"Hello, {name}! Welcome to Aviator!"

    logger.debug("Generated greeting: %s", greeting)
    return greeting
