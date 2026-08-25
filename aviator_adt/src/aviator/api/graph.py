"""API endpoints for graph operations."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Response, Security
from fastapi.responses import JSONResponse

from aviator.api.auth import require_authentication
from aviator.graph import aviator

# Lower httpx loglevel, used by google sdk
logging.getLogger("httpx").setLevel("WARN")

logger = logging.getLogger("aviator")
router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/mermaid.png", tags=["graph"])
async def get_graph_mermaid(
    user: Annotated[dict[str, Any], Security(require_authentication("graph_mermaid"))],  # noqa: ARG001
) -> Response:
    """Return the graph - rendered as png."""

    graph = await aviator.get_graph()
    png_bytes = graph.get_graph().draw_mermaid_png()
    return Response(content=png_bytes, media_type="image/png")


@router.get("/export", tags=["graph"])
async def get_graph_export(
    user: Annotated[dict[str, Any], Security(require_authentication("graph_export"))],  # noqa: ARG001
) -> JSONResponse:
    """Export the currently running graph as JSON."""

    graph = await aviator.get_graph()
    test = graph.get_graph().to_json()
    return JSONResponse(content=test)
