"""Tool for generating Vega-Lite format charts."""

import json
import logging
from typing import Annotated, Any, Literal

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _is_number(val: object) -> bool:
    """Check if a value can be converted to a number."""
    try:
        float(val)
    except (TypeError, ValueError):
        return False
    else:
        return True


def create_vega_lite_spec(
    chart_type: str,
    data: list[dict[str, Any]],
    x_field: str = "",
    y_field: str = "",
    x_type: str = "",
    y_type: str = "",
    color_field: str = "",
    title: str = "",
    x_title: str = "",
    y_title: str = "",
    width: int = 400,
    height: int = 300,
) -> dict[str, Any]:
    """Create a Vega-Lite specification from the request parameters.

    Args:
        chart_type: Type of chart to generate
        data: Array of data objects
        x_field: Name of the field to use for x-axis
        y_field: Name of the field to use for y-axis
        x_type: Data type for x-axis
        y_type: Data type for y-axis
        color_field: Optional field name for color encoding
        title: Title for the chart
        x_title: Custom title for x-axis
        y_title: Custom title for y-axis
        width: Width of the chart in pixels
        height: Height of the chart in pixels

    Returns:
        dict: Valid Vega-Lite specification

    """
    # Base specification
    spec: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": data},
        "width": width,
        "height": height,
    }

    # Add title if provided
    if title:
        spec["title"] = title

    # Build encoding based on chart type
    encoding: dict[str, Any] = {}

    # Handle different chart types
    mark_type = chart_type

    if chart_type == "pie":
        mark_type = "arc"
        if not data:
            msg = "Pie chart requires non-empty data"
            raise ValueError(msg)

        detected_x_field = x_field
        detected_y_field = y_field
        fields = list(data[0].keys())

        # --- Analyze fields ---
        field_stats = {}
        for f in fields:
            stats = {"has_numeric": False, "has_non_numeric": False}
            for row in data:
                val = row.get(f)
                if val is None:
                    continue
                if _is_number(val):
                    stats["has_numeric"] = True
                else:
                    stats["has_non_numeric"] = True
                if stats["has_numeric"] and stats["has_non_numeric"]:
                    break
            field_stats[f] = stats

        # --- Detect theta (value) ---
        if not detected_y_field:
            detected_y_field = next((f for f, s in field_stats.items() if s["has_numeric"]), None)

        # --- Detect color (category) ---
        if not detected_x_field:
            detected_x_field = next(
                (f for f, s in field_stats.items() if s["has_non_numeric"] and f != detected_y_field), None
            )

        # --- Fallback index (only if needed) ---
        if not detected_x_field:
            data = [{**row, "__index__": str(i + 1)} for i, row in enumerate(data)]
            detected_x_field = "__index__"

        # --- Human-readable titles ---
        legend_title = "Slice Index" if detected_x_field == "__index__" else detected_x_field.replace("_", " ").title()

        value_title = detected_y_field.replace("_", " ").title() if detected_y_field else None

        # --- Encoding ---
        if detected_y_field:
            encoding["theta"] = {
                "field": detected_y_field,
                "type": "quantitative",
                "aggregate": "sum",  # ✅ correct default for pie charts
                "title": value_title,
            }

        encoding["color"] = {"field": detected_x_field, "type": "nominal", "legend": {"title": legend_title}}

        # --- Tooltip ---
        encoding["tooltip"] = [{"field": detected_x_field, "type": "nominal", "title": legend_title}]

        if detected_y_field:
            encoding["tooltip"].append({"field": detected_y_field, "type": "quantitative", "title": value_title})
    elif chart_type == "histogram":
        # Histogram requires bin transformation
        if x_field:
            resolved_x_type = x_type or "quantitative"
            encoding["x"] = {
                "field": x_field,
                "type": resolved_x_type,
                "bin": True,
            }
            if x_title:
                encoding["x"]["title"] = x_title
        encoding["y"] = {"aggregate": "count", "type": "quantitative"}
        if y_title:
            encoding["y"]["title"] = y_title
        mark_type = "bar"
    elif chart_type == "scatter":
        mark_type = "point"
    else:
        # Standard x-y charts
        if x_field:
            resolved_x_type = x_type or "nominal"
            encoding["x"] = {
                "field": x_field,
                "type": resolved_x_type,
            }
            if x_title:
                encoding["x"]["title"] = x_title

        if y_field:
            resolved_y_type = y_type or "quantitative"
            encoding["y"] = {
                "field": y_field,
                "type": resolved_y_type,
            }
            if y_title:
                encoding["y"]["title"] = y_title

    # Add color encoding if specified
    if color_field and chart_type != "pie":
        encoding["color"] = {"field": color_field, "type": "nominal"}

    spec["mark"] = mark_type
    spec["encoding"] = encoding

    return spec


@tool
def generate_vega_lite_chart(
    chart_type: Annotated[
        Literal["bar", "line", "area", "scatter", "pie", "point", "boxplot", "histogram"],
        "Type of chart to generate. Options: bar, line, area, scatter, pie, point, boxplot, histogram",
    ],
    data: Annotated[
        list[dict[str, Any]],
        "Array of data objects. Each object should have key-value pairs representing the data fields.",
    ],
    x_field: Annotated[
        str,
        "Name of the field to use for x-axis. Required for most chart types.",
    ] = "",
    y_field: Annotated[
        str,
        "Name of the field to use for y-axis. Required for most chart types.",
    ] = "",
    x_type: Annotated[
        Literal["quantitative", "temporal", "ordinal", "nominal", ""],
        "Data type for x-axis: quantitative (numbers), temporal (dates), ordinal (ordered categories), nominal (unordered categories)",
    ] = "",
    y_type: Annotated[
        Literal["quantitative", "temporal", "ordinal", "nominal", ""],
        "Data type for y-axis: quantitative (numbers), temporal (dates), ordinal (ordered categories), nominal (unordered categories)",
    ] = "",
    color_field: Annotated[
        str,
        "Optional field name to use for color encoding (for grouping/categorization)",
    ] = "",
    title: Annotated[
        str,
        "Title for the chart",
    ] = "",
    x_title: Annotated[
        str,
        "Custom title for x-axis",
    ] = "",
    y_title: Annotated[
        str,
        "Custom title for y-axis",
    ] = "",
    width: Annotated[
        int,
        "Width of the chart in pixels",
    ] = 400,
    height: Annotated[
        int,
        "Height of the chart in pixels",
    ] = 300,
) -> str:
    """Generate a Vega-Lite format chart specification based on the provided data and parameters.

    This tool creates interactive charts in Vega-Lite JSON format. Use this when users ask to:
    - Create, generate, make, or show a chart, graph, or visualization
    - Display data visually
    - Plot data

    The tool supports various chart types: bar, line, area, scatter, pie, point, boxplot, and histogram.

    USAGE GUIDELINES:
    1. When users ask to create, generate, show, plot, or display a chart, graph, or visualization, use this tool.
    2. Extract the data from the context or previous tool responses to populate the chart data.
    3. Choose the appropriate chart type based on the user's request or the nature of the data:
       - Use 'bar' for comparisons or categorical data
       - Use 'line' for trends over time or continuous data
       - Use 'scatter' or 'point' for showing relationships between variables
       - Use 'pie' or 'arc' for showing proportions or percentages
       - Use 'area' for cumulative values over time
    4. Ensure data types (x_type, y_type) match the data: 'quantitative' for numbers, 'temporal' for dates, 'nominal' for categories.
    5. Always include meaningful titles and axis labels when generating charts.
    6. The tool returns a Vega-Lite JSON specification that can be rendered as an interactive chart.

    Returns:
        str: JSON string containing the complete Vega-Lite specification

    Example:
        To create a bar chart of sales by category:
        chart_type="bar"
        data=[{"category": "A", "value": 10}, {"category": "B", "value": 20}]
        x_field="category"
        y_field="value"
        title="Sales by Category"

    """
    # This tool uses flat argument structure (individual parameters) rather than nested/structured
    # arguments for compatibility with models like llama3.3 that fail to generate nested arguments properly.
    try:
        logger.debug(
            "generate_vega_lite_chart called with chart_type=%s, x_field=%s, y_field=%s, data=%s",
            chart_type,
            x_field,
            y_field,
            data,
        )
        spec = create_vega_lite_spec(
            chart_type=chart_type,
            data=data,
            x_field=x_field,
            y_field=y_field,
            x_type=x_type,
            y_type=y_type,
            color_field=color_field,
            title=title,
            x_title=x_title,
            y_title=y_title,
            width=width,
            height=height,
        )
        logger.info("Generated Vega-Lite chart: %s", chart_type)
        logger.debug("Vega-Lite spec generated: %s", json.dumps(spec, indent=2))
        return json.dumps(spec, indent=2)
    except Exception as e:
        logger.exception("Error generating Vega-Lite chart")
        return f"Error generating chart: {e!s}"


object.__setattr__(
    generate_vega_lite_chart, "tags", ["charts", "visualization", "graphs", "data", "analytics", "vega-lite"]
)
object.__setattr__(
    generate_vega_lite_chart,
    "examples",
    [
        "Create a chart showing sales trends",
        "Visualize this data as a bar chart",
        "Generate a line chart from this dataset",
        "Create a pie chart to show proportions",
    ],
)
