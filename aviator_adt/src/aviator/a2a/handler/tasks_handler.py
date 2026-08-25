"""A2A tasks/* method handlers — ``tasks/get`` and ``tasks/cancel``.

Single Responsibility: read task state from the DB and map it to spec-compliant
``Task`` Pydantic models.  No graph invocation happens here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aviator.a2a.models import (
    Artifact,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from aviator.a2a.task_runner import cancel_task
from aviator.a2a.task_service import task_service
from aviator.exceptions import TaskNotCancelableError, TaskNotFoundError
from aviator.services.tenant import tenant_id_to_schema_name

if TYPE_CHECKING:
    from fastapi import Request

    from aviator.database.models import A2ATask

logger = logging.getLogger(__name__)


class TasksHandler:
    """Handles ``tasks/get`` and ``tasks/cancel`` A2A methods.

    Both handlers share the same callable signature as every other registered
    handler so the router can dispatch to them without special casing.
    """

    async def handle_get(
        self,
        payload: dict,
        request: Request,  # noqa: ARG002
        user: dict,
    ) -> Task:
        """Return the current ``Task`` for the given task ID.

        Args:
            payload: JSON-RPC ``params`` dict containing ``id``.
            request: Unused — present for uniform handler signature.
            user:    Authenticated user; used to resolve the tenant schema.

        Raises:
            TaskNotFoundError: If no task with the given ID exists.

        """
        task_id = self._extract_task_id(payload)
        schema_name = tenant_id_to_schema_name(user.get("tenantId"))

        row = task_service.get(task_id, schema_name)

        if row is None:
            msg = f"Task '{task_id}' not found"
            raise TaskNotFoundError(msg)

        return self._row_to_task(row)

    async def handle_cancel(
        self,
        payload: dict,
        request: Request,  # noqa: ARG002
        user: dict,
    ) -> Task:
        """Cancel an in-progress task and return the updated ``Task``.

        Args:
            payload: JSON-RPC ``params`` dict containing ``id``.
            request: Unused — present for uniform handler signature.
            user:    Authenticated user; used to resolve the tenant schema.

        Raises:
            TaskNotFoundError:    If no task with the given ID exists.
            TaskNotCancelableError: If the task is already in a terminal state.

        """
        task_id = self._extract_task_id(payload)
        schema_name = tenant_id_to_schema_name(user.get("tenantId"))

        row = task_service.get(task_id, schema_name)

        if row is None:
            msg = f"Task '{task_id}' not found"
            raise TaskNotFoundError(msg)

        if row.state not in {TaskState.SUBMITTED, TaskState.WORKING}:
            msg = f"Task '{task_id}' is in state '{row.state}' and cannot be cancelled"
            raise TaskNotCancelableError(msg)

        # Cancel the asyncio background task (best-effort).
        cancel_task(task_id)

        # Persist the canceled state.
        updated = task_service.cancel(task_id, schema_name)

        return self._row_to_task(updated or row)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_task_id(payload: dict) -> str:
        """Extract the task ``id`` from a JSON-RPC params dict."""
        task_id = payload.get("id") or (payload.get("params") or {}).get("id")
        if not task_id:
            msg = "Missing required field: 'id'"
            raise ValueError(msg)
        return str(task_id)

    @staticmethod
    def _row_to_task(row: A2ATask) -> Task:
        """Map an ``A2ATask`` ORM row to a spec-compliant ``Task`` model."""
        artifacts: list[Artifact] = []
        if row.artifacts and (result_text := row.artifacts.get("result")):
            artifacts = [
                Artifact(
                    name="response",
                    parts=[TextPart(text=result_text)],
                )
            ]

        return Task(
            id=row.id,
            contextId=row.context_id,
            status=TaskStatus(state=TaskState(row.state)),
            artifacts=artifacts,
        )
