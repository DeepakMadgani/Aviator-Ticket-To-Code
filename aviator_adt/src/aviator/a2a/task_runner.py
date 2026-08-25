"""A2A background task runner.

Wraps ``A2AChatHandler.process()`` in an asyncio background task so that
``message/send`` and ``message/stream`` can return a ``Task`` immediately
without blocking the HTTP response.

Design
------
* ``start_task()``  — schedules the coroutine on the running event loop.
* ``cancel_task()`` — cancels the asyncio ``Task`` via the in-memory registry.
* The runner updates the ``A2ATask`` DB row at each lifecycle transition so
  that ``tasks/get`` and the SSE stream always reflect the current state.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING

from aviator.a2a.models import TaskState
from aviator.a2a.task_service import task_service

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

# In-memory map of task_id → asyncio.Task.
# Used only for cancellation; the authoritative state lives in the DB.
_running: dict[str, asyncio.Task] = {}


async def _run(
    task_id: str,
    handler: object,
    payload: dict,
    request: Request,
    user: dict,
    schema_name: str | None,
) -> None:
    """Execute ``handler.process()`` and persist the result to the DB row."""
    try:
        logger.info("[A2A task] Task %s starting — schema=%s", task_id, schema_name)
        task_service.update_state(task_id, schema_name, TaskState.WORKING)
        logger.info("[A2A task] Task %s → working (calling /v1/chat)", task_id)

        # Delegate to A2AChatHandler.process() — calls /v1/chat internally.
        response = await handler.process(payload, request, user)  # type: ignore[union-attr]

        logger.info("[A2A task] Task %s — /v1/chat responded successfully", task_id)

        # Determine final state from LangGraph interrupt flag in context.
        interrupted = False
        if response.context:
            with contextlib.suppress(json.JSONDecodeError, AttributeError):
                interrupted = json.loads(response.context).get("interrupt", False)

        state = TaskState.INPUT_REQUIRED if interrupted else TaskState.COMPLETED
        logger.info("[A2A task] Task %s → %s%s", task_id, state, " (LangGraph interrupt)" if interrupted else "")

        artifacts = {
            "result": response.result or "",
            "references": response.references or [],
            "context": response.context or "",
            "where": [w.model_dump() if hasattr(w, "model_dump") else w for w in (response.where or [])],
        }

        if interrupted:
            task_service.update_state(task_id, schema_name, TaskState.INPUT_REQUIRED, artifacts=artifacts)
        else:
            task_service.update_state(task_id, schema_name, TaskState.COMPLETED, artifacts=artifacts)

    except asyncio.CancelledError:
        logger.info("[A2A task] Task %s → canceled (asyncio cancellation received)", task_id)
        task_service.update_state(task_id, schema_name, TaskState.CANCELED)

    except Exception:
        logger.exception("[A2A] Task %s failed", task_id)
        task_service.update_state(task_id, schema_name, TaskState.FAILED, error="Internal error — see server logs")

    finally:
        _running.pop(task_id, None)


def start_task(
    task_id: str,
    handler: object,
    payload: dict,
    request: Request,
    user: dict,
    schema_name: str | None,
) -> asyncio.Task:
    """Schedule the chat handler as a background asyncio task.

    Returns the ``asyncio.Task`` for callers that want to await or inspect it.
    """
    coro = _run(task_id, handler, payload, request, user, schema_name)
    asyncio_task = asyncio.create_task(coro, name=f"a2a-{task_id}")
    _running[task_id] = asyncio_task
    logger.debug("[A2A] Background task %s started", task_id)
    return asyncio_task


def cancel_task(task_id: str) -> bool:
    """Request cancellation of a running asyncio task.

    Returns ``True`` if the task was found and cancellation was requested,
    ``False`` if there is no running asyncio task with that ID (it may have
    already finished or never started).
    """
    asyncio_task = _running.get(task_id)
    if asyncio_task and not asyncio_task.done():
        asyncio_task.cancel()
        logger.debug("[A2A] Cancellation requested for task %s", task_id)
        return True
    return False
