"""A2AChatHandler — bridges the A2A protocol to the internal /v1/chat endpoint.

.. Note::
    Uses an internal HTTP call because the chat logic is currently embedded in the
    FastAPI route handler ``post_chat()`` and requires moving that logic to a shared
    service class that can be reused by both the chat and A2A APIs.
    TODO: extract chat logic to service class and call it directly here.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request

from aviator.a2a.handler.ws_streaming import ws_to_a2a_sse_stream
from aviator.a2a.models import (
    A2AAgentInvokeResponse,
    Task,
    TaskState,
    TaskStatus,
)
from aviator.a2a.task_runner import start_task
from aviator.a2a.task_service import task_service
from aviator.a2a.util import build_base_url, build_forward_headers
from aviator.services.tenant import tenant_id_to_schema_name

if TYPE_CHECKING:
    from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)


class A2AChatHandler:
    """Handles A2A chat requests by delegating to the internal chat API."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Initialise the handler. Inject *client* to override the default (useful in tests)."""
        self._client = client

    # ── Public handler methods (registered in MethodRegistry) ────────────────

    async def handle_message_send(
        self,
        payload: dict,
        request: Request,
        user: dict,
    ) -> Task:
        """Create a ``Task``, fire ``process()`` in background, return ``Task`` immediately.

        This is the primary A2A method handler for ``message/send``.
        The graph runs asynchronously; poll ``tasks/get`` for the result.

        Returns:
            A ``Task`` object with submitted status.

        """
        task_id, schema_name, context_id = self._create_and_persist_task(payload, user)
        start_task(task_id, self, payload, request, user, schema_name)
        return Task(
            id=task_id,
            contextId=context_id,
            status=TaskStatus(state=TaskState.SUBMITTED),
        )

    async def handle_message_stream(
        self,
        payload: dict,
        request: Request,
        user: dict,
    ) -> StreamingResponse:
        """Create a Task, then stream A2A spec-compliant events backed by the internal /v1/chat/stream.

        Stream order (A2A spec §4.1.2):
          1. ``Task`` object (state: submitted)
          2. ``TaskStatusUpdateEvent`` (state: working)
          3. ``TaskArtifactUpdateEvent`` — one per token/chunk from /v1/chat/stream
          4. ``TaskStatusUpdateEvent`` (state: completed, final=True)

        Returns:
            A ``StreamingResponse`` that streams A2A spec-compliant events.

        """
        task_id, schema_name, context_id = self._create_and_persist_task(payload, user)

        # Build ws payload in the format expected by websockets.py
        raw_context = payload.get("context") or {}
        if isinstance(raw_context, str):
            try:
                raw_context = json.loads(raw_context)
            except ValueError:
                raw_context = {}

        ws_payload = {
            "content": (payload.get("messages") or [{}])[-1].get("content", ""),
            "context": raw_context,
            "where": payload.get("where", []),
            "user": user,
            "messages": payload.get("messages", []),
        }
        base = build_base_url(request)
        parsed = urlparse(base)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        ws_url = urlunparse(parsed._replace(scheme=ws_scheme, path="/v1/chat/stream"))
        headers = build_forward_headers(request)

        return await ws_to_a2a_sse_stream(
            task_id=task_id,
            context_id=context_id,
            schema_name=schema_name,
            ws_url=ws_url,
            ws_payload=ws_payload,
            headers=headers,
        )

    async def process(
        self,
        payload: dict,
        request: Request,
        _user: dict,
    ) -> A2AAgentInvokeResponse:
        """Forward the A2A payload to ``/v1/chat`` and return the mapped response.

        Called by the background task runner — not invoked directly by the router.

        Returns:
            An ``A2AAgentInvokeResponse`` containing the mapped chat result and references.

        """
        raw = await self._call_v1_chat(
            build_base_url(request),
            payload,
            build_forward_headers(request),
            dict(request.cookies),
        )
        return self._map_response(raw)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _create_and_persist_task(
        self,
        payload: dict,
        user: dict,
    ) -> tuple[str, str | None, str | None]:
        """Create a new ``A2ATask`` DB row and return ``(task_id, schema_name, context_id)``.

        ``context_id`` is the LangGraph ``thread_id`` extracted from ``payload.context``
        (a JSON string like ``'{"thread_id": "uuid"}``).  This is set by ``normalize_input``
        and is always present after that validator runs.

        Returns:
            A tuple of (task_id, schema_name, context_id) where context_id is the LangGraph thread_id.

        """
        task_id = str(uuid4())
        schema_name = tenant_id_to_schema_name(user.get("tenantId"))

        # payload["context"] is always '{"thread_id": "<uuid>"}' — set by normalize_input
        context_id: str | None = None
        if raw_context := payload.get("context"):
            context_id = json.loads(raw_context).get("thread_id")

        task_service.create(task_id, context_id, schema_name)
        logger.info("[A2AChatHandler] Task %s created schema=%s context_id=%s", task_id, schema_name, context_id)
        return task_id, schema_name, context_id

    async def _call_v1_chat(
        self,
        base_url: str,
        payload: dict,
        headers: dict,
        cookies: dict,
    ) -> dict:
        """POST to ``/v1/chat``, using the injected client when available."""
        if self._client is not None:
            res = await self._client.post(
                f"{base_url}/v1/chat",
                json=payload,
                headers=headers,
                cookies=cookies,
            )
        else:
            async with httpx.AsyncClient(timeout=60.0) as client:
                res = await client.post(
                    f"{base_url}/v1/chat",
                    json=payload,
                    headers=headers,
                    cookies=cookies,
                )
        if res.status_code >= 400:
            raise HTTPException(status_code=res.status_code, detail=res.text)
        return res.json()

    def _map_response(self, raw: dict) -> A2AAgentInvokeResponse:
        """Map the raw ``/v1/chat`` response dict to an :class:`A2AAgentInvokeResponse`."""
        return A2AAgentInvokeResponse(
            result=raw.get("result"),
            context=raw.get("context"),
            where=raw.get("where", []),
            references=raw.get("references", []),
        )
