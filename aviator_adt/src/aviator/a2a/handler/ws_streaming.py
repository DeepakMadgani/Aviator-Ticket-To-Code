"""WebSocket-to-SSE relay for A2A streaming — A2A spec compliant hybrid approach.

Emission order (Task lifecycle stream):
  1. ``Task`` object  — initial state (submitted → working)
  2. ``TaskArtifactUpdateEvent`` — one per token/chunk from the /v1/chat/stream stream
  3. ``TaskStatusUpdateEvent``  — final (completed / failed), final=True

This gives real-time token streaming while staying spec-compliant.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import websockets
import websockets.exceptions
from starlette.responses import StreamingResponse

from aviator.a2a.models import (
    CONTENT_TYPE_SSE,
    Artifact,
    Task,
    TaskArtifactUpdate,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdate,
    TaskStatusUpdateEvent,
    TextPart,
)
from aviator.a2a.task_service import task_service

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


def _sse(event_type: str, data: dict) -> str:
    """Format a single SSE frame."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def ws_to_a2a_sse_stream(
    task_id: str,
    context_id: str | None,
    schema_name: str | None,
    ws_url: str,
    ws_payload: dict,
    headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """Stream A2A spec-compliant SSE events backed by the internal /v1/chat/stream WebSocket."""

    async def event_stream() -> AsyncGenerator[str]:
        logger.info("[A2A stream] Task %s starting — contextId=%s ws_url=%s", task_id, context_id, ws_url)

        # ── 1. Emit initial Task (state: submitted) ──────────────────────────
        yield _sse(
            "task",
            Task(
                id=task_id,
                contextId=context_id,
                status=TaskStatus(state=TaskState.SUBMITTED),
            ).model_dump(),
        )
        logger.info("[A2A stream] Task %s → submitted (SSE task event emitted)", task_id)

        # ── 2. Update DB → working, emit status-update ────────────────────────
        task_service.update_state(task_id, schema_name, TaskState.WORKING)
        logger.info("[A2A stream] Task %s → working (DB updated, connecting to /v1/chat/stream)", task_id)

        yield _sse(
            "status-update",
            TaskStatusUpdateEvent(
                statusUpdate=TaskStatusUpdate(
                    taskId=task_id,
                    contextId=context_id,
                    status=TaskStatus(state=TaskState.WORKING),
                    final=False,
                )
            ).model_dump(),
        )

        # ── 3. Connect to /v1/chat/stream and relay each frame as artifact-update ─────────
        final_state = TaskState.COMPLETED
        final_answer: str | None = None  # Tracks the last type="ai" full answer for DB persistence
        token_count = 0
        try:
            async with websockets.connect(
                ws_url,
                additional_headers=headers or {},
                open_timeout=10,
            ) as ws:
                logger.info("[A2A stream] Task %s — WebSocket connected to %s", task_id, ws_url)
                await ws.send(json.dumps(ws_payload))
                try:
                    async for raw_msg in ws:
                        try:
                            msg = json.loads(raw_msg)
                        except ValueError:
                            continue

                        # Native /v1/chat/stream events use "type"; plugin custom events use "event"
                        msg_type = msg.get("type") or msg.get("event", "")

                        # Skip housekeeping frames — no value to A2A client
                        if msg_type in ("feedback", "final", "metadata"):
                            continue

                        # Native /v1/chat/stream: "content"; plugin chunk events: "text"; done event: "answer"
                        content = msg.get("content") or msg.get("text") or msg.get("answer", "")

                        if not content:
                            # Progress / routing events carry useful status info but no content.
                            # Relay them as status-update progress labels.
                            progress_label = (
                                msg.get("label") or msg.get("message") or (msg.get("routing_info") and msg_type)
                            )
                            if progress_label and isinstance(progress_label, str):
                                yield _sse(
                                    "status-update",
                                    TaskStatusUpdateEvent(
                                        statusUpdate=TaskStatusUpdate(
                                            taskId=task_id,
                                            contextId=context_id,
                                            status=TaskStatus(state=TaskState.WORKING),
                                            final=False,
                                            progressLabel=progress_label,
                                        )
                                    ).model_dump(),
                                )
                            continue

                        # "ai" (native) or "done" (plugin) = fully assembled final answer.
                        # Store for DB persistence.
                        if msg_type in ("ai", "done"):
                            final_answer = msg.get("answer") or content
                            logger.info(
                                "[A2A stream] Task %s — final answer captured (%d chars, %d tokens streamed)",
                                task_id,
                                len(final_answer),
                                token_count,
                            )

                        token_count += 1
                        # Emit each token/chunk as an artifact-update
                        yield _sse(
                            "artifact-update",
                            TaskArtifactUpdateEvent(
                                artifactUpdate=TaskArtifactUpdate(
                                    taskId=task_id,
                                    contextId=context_id,
                                    artifact=Artifact(
                                        name="response",
                                        parts=[TextPart(text=content)],
                                        metadata={"node": msg.get("name"), "type": msg_type},
                                    ),
                                )
                            ).model_dump(),
                        )

                except websockets.exceptions.ConnectionClosedOK:
                    logger.info("[A2A stream] Task %s — WebSocket closed cleanly", task_id)
                except websockets.exceptions.ConnectionClosedError:
                    logger.debug("[ws_proxy] Connection closed without close frame — normal end")

        except Exception:
            logger.exception("[ws_proxy] WebSocket connection error")
            final_state = TaskState.FAILED
            yield _sse(
                "status-update",
                TaskStatusUpdateEvent(
                    statusUpdate=TaskStatusUpdate(
                        taskId=task_id,
                        contextId=context_id,
                        status=TaskStatus(state=TaskState.FAILED),
                        final=True,
                    )
                ).model_dump(),
            )
            # Persist failed state
            task_service.update_state(task_id, schema_name, TaskState.FAILED)
            return

        # ── 4. Persist final state + artifact and emit completed event ──────────
        logger.info("[A2A stream] Task %s → %s (persisting to DB)", task_id, final_state.value)
        task_service.update_state(
            task_id, schema_name, final_state, artifacts={"result": final_answer} if final_answer else None
        )

        yield _sse(
            "status-update",
            TaskStatusUpdateEvent(
                statusUpdate=TaskStatusUpdate(
                    taskId=task_id,
                    contextId=context_id,
                    status=TaskStatus(state=final_state),
                    final=True,
                )
            ).model_dump(),
        )

    return StreamingResponse(
        event_stream(),
        media_type=CONTENT_TYPE_SSE,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
