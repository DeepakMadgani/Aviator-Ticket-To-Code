"""Entry point for Aviator ADT command-line interface."""

import asyncio
import json
import os
import sys


def draw_graph() -> None:
    """Draw the state graph in Mermaid format."""

    os.environ["CHECKPOINTER"] = "memory"

    from aviator.graph import ContentAviatorAgent

    aviator = ContentAviatorAgent()
    graph = asyncio.run(aviator.get_graph())
    draw = graph.get_graph().draw_mermaid(with_styles=False)
    with open("graph_mermaid.md", "w") as f:
        f.write(draw)


def openapi() -> None:
    """Generate OpenAPI specification."""
    from aviator.main import app

    spec = app.openapi()
    with open("docs/openapi.json", "w") as f:
        json.dump(spec, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "draw":
        draw_graph()
    elif len(sys.argv) > 1 and sys.argv[1] == "openapi":
        openapi()
