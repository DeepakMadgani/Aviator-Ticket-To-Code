"""A2A utility helpers.

Pure, side-effect-free functions used across the A2A package:
  - URL / header construction
  - Tool-name ↔ skill-id / skill-name normalisation
"""

import logging
import re

from fastapi import Request

from aviator.settings import settings

logger = logging.getLogger(__name__)

# ── Request helpers ───────────────────────────────────────────────────────────


def build_base_url(request: Request) -> str:
    """Return the base URL for internal loopback calls (e.g. /v1/chat, /v1/chat/stream).

    Uses ``AVIATOR_CHAT_SERVICE_ENDPOINT`` (defaults to the cluster-internal
    service URL) so that calls stay within the service mesh and do not
    round-trip through the ingress/gateway.
    """
    base = settings.aviator_chat_service_endpoint.rstrip("/")
    url = f"{base}{settings.root_path}" if settings.root_path else base
    logger.debug(
        "[A2A] build_base_url: AVIATOR_CHAT_SERVICE_ENDPOINT=%s (incoming_scheme=%s netloc=%s)",
        url,
        request.url.scheme,
        request.url.netloc,
    )
    return url


def build_forward_headers(request: Request) -> dict[str, str]:
    """Forward auth headers from the incoming A2A request to the internal /v1/chat call."""
    headers: dict[str, str] = {"content-type": "application/json"}
    if auth := request.headers.get("authorization"):
        headers["authorization"] = auth
    if ticket := request.headers.get("otcsticket"):
        headers["otcsticket"] = ticket
    return headers


# ── Skill name helpers ────────────────────────────────────────────────────────


def to_skill_id(tool_name: str) -> str:
    """Normalise a tool name into a URL-safe, lowercase skill identifier."""
    normalised = re.sub(r"[^a-zA-Z0-9_]+", "_", tool_name).strip("_").lower()
    return normalised or "tool"


def to_skill_name(tool_name: str) -> str:
    """Convert a raw tool name into a human-readable title-cased skill name."""
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", tool_name) if p]
    return " ".join(part.capitalize() for part in parts) if parts else "Tool"
