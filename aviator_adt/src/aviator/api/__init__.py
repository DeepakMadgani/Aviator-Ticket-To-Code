from aviator.a2a.router import router as a2a_router

from .devtools import router as devtools_router
from .graph import router as graph_router
from .projects import router as projects_router
from .queue import router as queue_router
from .stats import router as stats_router
from .tenants import router as tenants_router
from .v1 import router as v1_router
from .websockets import router as websocket_router

__all__ = [
    "a2a_router",
    "devtools_router",
    "graph_router",
    "projects_router",
    "queue_router",
    "stats_router",
    "tenants_router",
    "v1_router",
    "websocket_router",
]
