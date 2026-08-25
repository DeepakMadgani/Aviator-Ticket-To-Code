"""API version 1 router."""

import json
import logging

from fastapi import APIRouter, WebSocket
from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command
from langsmith import uuid7
from opentelemetry import trace
from psycopg import errors as pg_errors

from aviator.api.chat_history import ChatHistoryValidationError, ChatHistoryValidator
from aviator.database.checkpointer import checkpointer_schema
from aviator.graph import aviator
from aviator.metrics import socket_counter
from aviator.models import WhereClauseReferenceModel
from aviator.plugins import get_auth_handler_ws
from aviator.services.tenant import tenant_id_to_schema_name
from aviator.services.usage_tracking.llm_usage import (
    LLMUsageTrackingCallbackHandler,
    begin_llm_usage_collection,
    finish_llm_usage_collection,
)
from aviator.services.usage_tracking.recorder import record_transaction
from aviator.utils.document_mentions import extract_document_mentions
from aviator.utils.langfuse import callbacks, langfuse, propagate_attributes
from aviator.utils.version_util import get_aviator_version

# Lower httpx loglevel, used by google sdk
logging.getLogger("httpx").setLevel("WARN")

tracer = trace.get_tracer(__name__)
router = APIRouter(tags=["v1"])
logger = logging.getLogger("aviator")

# Background task references kept to prevent GC of fire-and-forget tasks
_background_tasks: set = set()


def _track_usage(tenant_id: str | None, transaction_type: str, **kwargs: object) -> None:
    """Fire-and-forget usage recording."""
    import asyncio

    task = asyncio.create_task(record_transaction(tenant_id=tenant_id, transaction_type=transaction_type, **kwargs))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.websocket("/v1/chat/stream")
@tracer.start_as_current_span("Content Aviator")
async def websocket_ws(websocket: WebSocket) -> None:
    """Implement WebSocket handler for chat."""

    async def finalize_connection() -> None:
        """Finalize the WebSocket connection."""

        # When the interaction is done send the feedback request
        await websocket.send_text(json.dumps({"type": "feedback", "enabled": False}))
        # Indicate the end of the interaction
        await websocket.send_text(json.dumps({"type": "final"}))

    await websocket.accept()
    try:
        socket_counter.inc("/v1/chat/stream")
        async for message in websocket.iter_text():
            with langfuse.start_as_current_observation(name="Content Aviator WS") as span:
                try:
                    message = json.loads(message)
                except Exception as e:
                    await websocket.send_text(json.dumps({"error": f"Invalid JSON format - {e}"}))
                    break

                if "content" not in message:
                    await websocket.send_text(json.dumps({"error": "Missing 'content' in message"}))
                    break

                context = message.get("context", {})
                log_level = message.get("log_level")

                # Check for AviatorVersion request
                if message.get("content") == "[AviatorVersion]":
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "ai",
                                "name": None,
                                "content": get_aviator_version(),
                                "metadata": {"thread_id": context.get("thread_id", "")},
                            }
                        )
                    )

                    await finalize_connection()
                    break

                content = message.get("content")
                mentioned_docs = extract_document_mentions(content)

                where = [WhereClauseReferenceModel.model_validate(w) for w in message.get("where", [])]

                # Perform authentication based on user payload
                auth_fn = get_auth_handler_ws()
                user = auth_fn(user=message.get("user", {})) if auth_fn else message.get("user", {})
                userid = (
                    user.get("login") or user.get("username") or user.get("name") or user.get("email") or user.get("id")
                )

                thread_id = context.get("thread_id", str(uuid7()))
                has_thread_id = bool(context.get("thread_id"))

                # Use mentioned documents if available, otherwise use request where clause
                where = mentioned_docs or where

                # Backward compatibility: when no thread_id is provided and the client
                # sends the full conversation history in a "messages" array, forward
                # the entire history to the graph.
                ws_messages = message.get("messages", [])
                if not has_thread_id and ws_messages and len(ws_messages) > 1:
                    history = [(m.get("author", "user"), m.get("content", "")) for m in ws_messages]
                    try:
                        ChatHistoryValidator.validate(history)
                        graph_messages = history
                    except ChatHistoryValidationError as exc:
                        logger.warning("WebSocket chat history is invalid; falling back to single message: %s", exc)
                        graph_messages = None
                else:
                    graph_messages = None

                graph = await aviator.get_graph()

                # Route checkpointer to the tenant schema
                checkpointer_schema.set(tenant_id_to_schema_name(user.get("tenantId")))

                if "interrupt" in context and context["interrupt"] is True:
                    _input = Command(resume=content)
                else:
                    _input = {
                        "messages": graph_messages or ("human", content),
                        "where": where,
                        "user": user,
                        "caller": message.get("caller"),
                    }

                _trace_input = {"role": "human", "content": message.get("content"), "metadata": {"where": where}}
                span.update(input=_trace_input)
                span.set_trace_io(input=_trace_input)
                with propagate_attributes(
                    user_id=userid,
                    session_id=f"{thread_id}:{userid}" if userid else thread_id,
                    tags=["websocket", "Content Aviator Agent"],
                    trace_name="Content Aviator WS",
                ):
                    pass  # set trace-level attributes on the current span

                usage_token = begin_llm_usage_collection()
                llm_usage = None
                request_callbacks = [*callbacks, LLMUsageTrackingCallbackHandler()]

                try:
                    async for mode, payload in graph.astream(
                        input=_input,
                        config=RunnableConfig(
                            tags=["websocket"],
                            configurable={
                                # Internally, the graph keys sessions by both thread_id and user_id.
                                # The API only exposes thread_id; user_id is enforced server-side to prevent cross-user access.
                                "thread_id": f"{thread_id}:{userid}" if userid else thread_id,
                                "user": user,
                                "log_level": log_level,
                            },
                            callbacks=request_callbacks,
                        ),
                        stream_mode=["updates", "values", "messages", "custom"],
                    ):
                        if mode == "values":
                            values = payload

                        if mode == "updates" and "__interrupt__" in payload:
                            interrupts = payload.get("__interrupt__")
                            content = " ".join(str(i.value) for i in interrupts)
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "ai",
                                        "content": content,
                                        "interrupt": True,
                                    }
                                )
                            )

                        elif mode == "messages":
                            msg, metadata = payload

                            if msg.content and metadata.get("langgraph_node", "") == "format_answer":
                                if isinstance(msg.content, list):
                                    content = [item["text"] for item in msg.content if item.get("type") == "text"]
                                    content = "".join(content)
                                else:
                                    content = msg.content

                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "type": msg.type,
                                            "name": msg.name,
                                            "content": content,
                                            "metadata": metadata,
                                        }
                                    )
                                )

                        elif mode == "custom":
                            await websocket.send_text(json.dumps(payload))
                except pg_errors.InvalidSchemaName:
                    logger.warning("Tenant schema not found for WebSocket user '%s'", userid)
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "content": "Your tenant environment is not yet initialised. "
                                "Please contact your administrator.",
                            }
                        )
                    )
                    await finalize_connection()
                    break
                finally:
                    llm_usage = finish_llm_usage_collection(usage_token)

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "metadata",
                            "references": [ref.model_dump() for ref in values.get("references", [])],
                            "where": [w.model_dump(by_alias=True) for w in values.get("where", [])],
                            "context": {
                                "thread_id": thread_id,
                                "trace_id": span.trace_id,
                                "interrupt": "__interrupt__" in payload,
                            },
                        }
                    )
                )

                _trace_output = {
                    "role": "ai",
                    "content": content,
                    "metadata": {
                        "thread_id": f"{thread_id}:{userid}" if userid else thread_id,
                        "where": where,
                        "references": [ref.model_dump() for ref in values.get("references", [])],
                        "context": {
                            "thread_id": thread_id,
                            "trace_id": span.trace_id,
                            "interrupt": "__interrupt__" in payload,
                        },
                    },
                }
                span.update(output=_trace_output)
                span.set_trace_io(output=_trace_output)

            await finalize_connection()

            # Record websocket chat usage
            tenant_id = user.get("tenantId") if user else None
            _track_usage(
                tenant_id,
                "chat_ws",
                llm_total_requests=llm_usage.requests if llm_usage else 0,
                input_tokens=llm_usage.input_tokens if llm_usage else 0,
                output_tokens=llm_usage.output_tokens if llm_usage else 0,
            )

            # close the connection
            break

    except Exception as e:
        try:
            await websocket.send_text(json.dumps({"error": f"{e}"}))
        except Exception:
            logger.warning("Failed to send error message over WebSocket: %s ", e)

    finally:
        socket_counter.dec("/v1/chat/stream")
