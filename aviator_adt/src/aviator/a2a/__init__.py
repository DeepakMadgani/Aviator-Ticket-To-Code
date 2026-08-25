"""A2A (Agent-to-Agent) protocol package.

Public surface:
  - ``router``  — FastAPI ``APIRouter`` to mount in the main app.
  - ``models``  — All A2A Pydantic models (importable as ``aviator.a2a.models``).
"""

from aviator.a2a.router import router

__all__ = ["router"]
