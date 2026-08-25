"""Implementation of date and time related tools."""

import logging
from datetime import UTC, datetime

from langchain_core.tools import tool

from aviator.mcp.server.utils.decorator import mcp_expose

logger = logging.getLogger(__name__)


#
# TOOLS
#


@mcp_expose
@tool
def current_time() -> str:
    """Use this to get the current date and time, return it as a string."""

    return datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S") + " UTC"


object.__setattr__(current_time, "tags", ["datetime", "time", "date", "temporal", "utility"])
object.__setattr__(
    current_time,
    "examples",
    [
        "What is the current date?",
        "What time is it?",
        "Tell me the current date and time",
    ],
)
