"""Utility functions for Langfuse integration."""

import logging

from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler

logger = logging.getLogger(__name__)

callbacks = []


# Initialize Langfuse client
langfuse = get_client()

# Initialize Langfuse CallbackHandler for Langchain (tracing)
try:
    langfuse.auth_check()
    logger.info("Langfuse client initialized successfully.")
    langfuse_handler = CallbackHandler()
    callbacks = [langfuse_handler]
except Exception:
    logger.debug("Langfuse client initialization failed: %s")

__all__ = ["callbacks", "langfuse", "propagate_attributes"]
