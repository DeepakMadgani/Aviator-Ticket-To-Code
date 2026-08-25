"""Custom graph nodes and edges for the Aviator plugin.

This module contains all custom nodes that modify the conversation flow.
Each node function receives the state and returns the modified state.
"""

import logging
from typing import Annotated

from aviator.models import StateModel
from langgraph.prebuilt import InjectedState

logger = logging.getLogger(__name__)


def my_validation_node(state: Annotated[StateModel, InjectedState]) -> dict:
    """Validate user input before processing.

    Args:
        state: Current conversation state with messages, context, etc.

    Returns:
        Modified state dictionary

    """
    logger.info("Validating user input")

    # Access the conversation state
    # messages = state.messages  # List of messages
    query = state.query  # Current user query

    # Your validation logic here
    if len(query) > 1000:
        logger.warning("Query too long, truncating")
        return {"query": query[:1000]}

    # Return modified state or empty dict if no changes
    return {}
