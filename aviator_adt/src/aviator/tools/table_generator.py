"""Tool for generating markdown tables."""

import logging
from typing import Annotated, Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def create_markdown_table(
    headers: list[str],
    rows: list[dict[str, Any]],
) -> str:
    """Create a markdown table from headers and rows.

    Args:
        headers: Column headers
        rows: List of row dictionaries

    Returns:
        str: Markdown formatted table

    """
    if not headers or not rows:
        return ""

    # Create header row
    header_row = "| " + " | ".join(headers) + " |"

    # Create separator row with proper markdown formatting (3 dashes)
    separator = "|" + "|".join(["---" for _ in headers]) + "|"

    # Create data rows
    data_rows = []
    for row in rows:
        values = [str(row.get(h, "")) for h in headers]
        data_rows.append("| " + " | ".join(values) + " |")

    # Combine all parts
    table = "\n".join([header_row, separator] + data_rows)
    return table


@tool(
    description="Generate a markdown table from provided headers and row data. "
    "Use this when users ask to create, generate, make, or show a table.",
)
def generate_markdown_table(
    headers: Annotated[
        list[str],
        "List of column header names for the table",
    ],
    rows: Annotated[
        list[dict[str, Any]],
        "List of row data as dictionaries, where keys match header names",
    ],
    title: Annotated[
        str,
        "Optional title or caption for the table",
    ] = "",
) -> str:
    """Generate a markdown table from provided data.

    Use this when users ask to create, generate, make, or show a table.

    Args:
        headers: Column header names
        rows: Row data (list of dictionaries)
        title: Optional title for the table

    Returns:
        str: Markdown formatted table

    """
    try:
        # Validate inputs
        if not headers:
            logger.warning("generate_markdown_table: Empty headers provided")
            return "Error: Headers cannot be empty"

        if not rows:
            logger.warning("generate_markdown_table: Empty rows provided")
            return "Error: At least one row is required"

        # Create the table
        table = create_markdown_table(headers, rows)

        # Add title if provided
        if title:
            table = f"**{title}**\n\n{table}"

        logger.debug("generate_markdown_table: Successfully generated table with %d rows", len(rows))

    except Exception as e:
        logger.exception("generate_markdown_table: Error generating table")
        return f"Error generating table: {e!s}"
    else:
        return table


object.__setattr__(generate_markdown_table, "tags", ["tables", "formatting", "data", "presentation", "markdown"])
object.__setattr__(
    generate_markdown_table,
    "examples",
    [
        "Create a table from this data",
        "Format this information as a table",
        "Organize these items in a table",
        "Generate a comparison table",
    ],
)
