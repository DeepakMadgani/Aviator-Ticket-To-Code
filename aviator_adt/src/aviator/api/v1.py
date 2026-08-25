"""API version 1 router."""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Response, Security, status
from fastapi.responses import JSONResponse
from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command
from langsmith import uuid7
from opentelemetry import trace
from psycopg import errors as pg_errors

from aviator import models
from aviator.api.auth import require_authentication
from aviator.api.chat_history import ChatHistoryValidationError, ChatHistoryValidator
from aviator.celery import _get_document_routing_key, process_embedding_request, process_workspace_summary_request
from aviator.database.checkpointer import checkpointer_schema
from aviator.graph import aviator
from aviator.services.embeddings import EmbeddingsRegistry
from aviator.services.tenant import tenant_id_to_schema_name
from aviator.services.usage_tracking.llm_usage import (
    LLMUsageTrackingCallbackHandler,
    begin_llm_usage_collection,
    finish_llm_usage_collection,
)
from aviator.services.usage_tracking.recorder import record_transaction
from aviator.settings import settings
from aviator.tools.rag import api_rag_query
from aviator.utils.citations import replace_chunk_ids_with_citation_numbers
from aviator.utils.document_mentions import extract_document_mentions
from aviator.utils.langfuse import callbacks, langfuse, propagate_attributes
from aviator.utils.limiter import limiter
from aviator.utils.version_util import get_aviator_version

# Lower httpx loglevel, used by google sdk
logging.getLogger("httpx").setLevel("WARN")

tracer = trace.get_tracer(__name__)
router = APIRouter(prefix="/v1", tags=["v1"])
logger = logging.getLogger("aviator")

# Background task references kept to prevent GC of fire-and-forget tasks
_background_tasks: set = set()


def _track_usage(tenant_id: str | None, transaction_type: str, **kwargs: object) -> None:
    """Fire-and-forget usage recording — never blocks the response."""
    import asyncio

    task = asyncio.create_task(record_transaction(tenant_id=tenant_id, transaction_type=transaction_type, **kwargs))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.post(
    "/embeddings",
    tags=["embeddings"],
    responses={
        401: {"description": "Unauthorized"},
    },
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_embeddings(
    embedding_request: models.EmbeddingRequest,
    user: Annotated[dict[str, Any], Security(require_authentication("embeddings"))],  # noqa: ARG001
) -> JSONResponse:
    """Implement the Embeddings endpoint to hand JSON Activator requests."""

    request_dict = embedding_request.model_dump()
    if settings.vector_store == "memory":
        process_embedding_request(request_dict)
        process_workspace_summary_request(request_dict)
    elif settings.broker_consistent_hash_enabled:
        routing_key = _get_document_routing_key(request_dict) or ""
        process_embedding_request.apply_async(
            args=[request_dict],
            exchange=f"{settings.broker_queue_name}-hash",
            routing_key=routing_key,
        )
        process_workspace_summary_request.delay(request_dict)
    else:
        process_embedding_request.delay(request_dict)
        process_workspace_summary_request.delay(request_dict)

    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "accepted"})


@router.post(
    "/metadata",
    tags=["embeddings"],
    responses={
        401: {"description": "Unauthorized"},
    },
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_embeddings_metadata(
    embedding_request: models.EmbeddingRequest,
    user: Annotated[dict[str, Any], Security(require_authentication("metadata"))],  # noqa: ARG001
) -> JSONResponse:
    """Implement the Embeddings endpoint to hand JSON Activator requests."""

    # Indicate that this is a metadata driven request (no text splitting)
    request_dict = embedding_request.model_dump()
    if settings.vector_store == "memory":
        process_embedding_request(request_dict, is_metadata=True)
        process_workspace_summary_request(request_dict)
    elif settings.broker_consistent_hash_enabled:
        routing_key = _get_document_routing_key(request_dict) or ""
        process_embedding_request.apply_async(
            args=[request_dict],
            kwargs={"is_metadata": True},
            exchange=f"{settings.broker_queue_name}-hash",
            routing_key=routing_key,
        )
        process_workspace_summary_request.delay(request_dict)
    else:
        process_embedding_request.delay(request_dict, is_metadata=True)
        process_workspace_summary_request.delay(request_dict)

    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "accepted"})


@router.post(
    "/direct-embed",
    tags=["embeddings"],
    responses={
        401: {"description": "Unauthorized"},
    },
)
async def post_direct_embed(
    embedding_request: models.DirectEmbedRequest,
    user: Annotated[dict[str, Any], Security(require_authentication("direct_embed"))],  # noqa: ARG001
) -> models.DirectEmbedResponse:
    """Generate embeddings directly without storing them."""

    logger.info("Generating embeddings for %d texts", len(embedding_request.content))

    embeddings = EmbeddingsRegistry.get_embeddings()
    embeddings_list = await embeddings.aembed_documents(embedding_request.content)

    logger.debug("Generated %d embeddings", len(embeddings_list))

    return models.DirectEmbedResponse(
        vectors=embeddings_list,
        model=settings.embeddings_model,
    )


@router.post(
    "/feedback",
    tags=["chat"],
    responses={
        401: {"description": "Unauthorized"},
    },
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("15/minute")
async def post_feedback(
    request: Request,  # noqa: ARG001
    feedback: models.FeedbackRequestModel,
    user: Annotated[dict[str, Any], Security(require_authentication("feedback"))],  # noqa: ARG001
) -> Response:
    """Receive feedback for a chat response."""

    logger.info(
        "Received feedback: Question='%s', Answer='%s', Rating='%s'",
        feedback.question,
        feedback.answer,
        feedback.rating,
    )

    if feedback.context and not feedback.trace_id:
        try:
            context = json.loads(feedback.context)
            feedback.trace_id = context.get("trace_id", "")
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in feedback context: %s", feedback.context)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON in context") from e

    if feedback.trace_id:
        try:
            langfuse.auth_check()
            langfuse.create_score(
                name="thumbs_up",
                value=1 if feedback.rating == "UP" else 0,  # 0 or 1
                trace_id=feedback.trace_id,
                data_type="BOOLEAN",
                comment=feedback.comment,
            )
            return Response(status_code=status.HTTP_202_ACCEPTED)

        except Exception as e:
            logger.debug("Langfuse client initialization failed: %s")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Feedback service unavailable"
            ) from e

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing trace_id in feedback context")


@router.post(
    "/chat",
    tags=["chat"],
    responses={
        401: {"description": "Unauthorized"},
    },
)
@tracer.start_as_current_span("Content Aviator")
@limiter.limit("15/minute")
async def post_chat(
    request: Request,
    chat_request: models.ChatRequestModel,
    user: Annotated[dict[str, Any], Security(require_authentication("chat"))],
) -> models.ChatResponseModel:
    """Implement chat request endpoint."""

    messages = [(msg.author, msg.content) for msg in chat_request.messages]

    if messages[-1][1] == "[AviatorVersion]":
        return models.ChatResponseModel(
            result=get_aviator_version(),
            context="{}",
            where=[],
            references=[],
        )

    try:
        context = json.loads(chat_request.context) if chat_request.context else {}
    except json.JSONDecodeError:
        context = {}

    has_thread_id = bool(context.get("thread_id"))

    # Backward compatibility: when no thread_id is provided and the client sends
    # the full conversation history, forward the entire message list to the graph.
    # When a thread_id exists the checkpointer already holds the history, so only
    # the latest user message is needed.
    if not has_thread_id and len(messages) > 1:
        try:
            ChatHistoryValidator.validate(messages)
        except ChatHistoryValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        graph_messages = messages
    else:
        graph_messages = [messages[-1]]

    # The last user message is always the current query
    user_message = messages[-1]

    # Extract document mentions from the last user message
    mentioned_docs = extract_document_mentions(user_message[1])

    # Use mentioned documents if available, otherwise use request where clause
    where_clause = mentioned_docs or list(chat_request.where)

    userid = user.get("login") or user.get("username") or user.get("name") or user.get("email") or user.get("id")

    thread_id = context.get("thread_id", str(uuid7()))

    # Track original query for queryMetadata
    original_query = user_message[1]

    # Handle interrupt/resume logic
    if context.get("interrupt"):
        _input = Command(resume=graph_messages)

    # Handle normal chat request
    else:
        _input = {
            "messages": graph_messages,
            "where": where_clause,
            "user": user,
            "inlineCitation": chat_request.inlineCitation,
            "caller": chat_request.caller,
        }

    with langfuse.start_as_current_observation(name="Content Aviator POST") as span:
        _trace_input = {"role": "human", "content": user_message[1], "metadata": {"where": where_clause}}
        span.update(input=_trace_input)
        span.set_trace_io(input=_trace_input)
        with propagate_attributes(
            user_id=userid,
            session_id=f"{thread_id}:{userid}" if userid else thread_id,
            tags=["/v1/chat", "Content Aviator Agent"],
            trace_name="Content Aviator POST",
        ):
            pass  # set trace-level attributes on the current span

        graph = await aviator.get_graph()

        # Route checkpointer to the tenant schema
        checkpointer_schema.set(tenant_id_to_schema_name(user.get("tenantId")))

        # Ensure the Authorization header is always available in user context
        # so MCP OTDS_TOKEN auth can forward it to downstream MCP servers.
        auth_header = request.headers.get("authorization") or request.headers.get("otcsticket")
        if auth_header and "authorization" not in user:
            user = {**user, "authorization": auth_header}
            # Also update _input since it captured user before the mutation above
            if isinstance(_input, dict):
                _input = {**_input, "user": user}

        usage_token = begin_llm_usage_collection()
        llm_usage = None
        request_callbacks = [*callbacks, LLMUsageTrackingCallbackHandler()]

        try:
            response = await graph.ainvoke(
                input=_input,
                config=RunnableConfig(
                    tags=["/v1/chat"],
                    configurable={
                        # Internally, the graph keys sessions by both thread_id and user_id.
                        # The API only exposes thread_id; user_id is enforced server-side to prevent cross-user access.
                        "thread_id": f"{thread_id}:{userid}" if userid else thread_id,
                        "user": user,
                    },
                    callbacks=request_callbacks,
                ),
            )
        except pg_errors.InvalidSchemaName:
            logger.warning("Tenant schema not found for user '%s'", userid)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Your tenant environment is not yet initialised. Please contact your administrator.",
            ) from None
        finally:
            llm_usage = finish_llm_usage_collection(usage_token)

        # Detect a __interrupt__ which indicates a HITL is required
        if "__interrupt__" in response:
            interrupts = response.get("__interrupt__")
            result = " ".join(str(i.value) for i in interrupts)

        # Handle normal response
        else:
            messages = response.get("messages")

            if not messages:
                return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

            answer = messages[-1]

            if not answer.content:
                return HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="There ware an error during the chat request. Please try again...",
                )
            result = answer.text

        context = {
            "thread_id": thread_id,
            "interrupt": "__interrupt__" in response,
            "trace_id": span.trace_id,
        }

        # Extract query metadata for LLM contract compliance
        used_query = original_query
        if response.get("query"):
            used_query = response["query"]

        query_metadata = models.QueryMetadataModel(
            originalQuery=original_query,
            usedQuery=used_query,
        )

        references = response.get("references", [])
        state_where = response.get("where")
        output_where = state_where if isinstance(state_where, list) else where_clause
        rendered_result = replace_chunk_ids_with_citation_numbers(result, references)

        chat_response = models.ChatResponseModel(
            result=rendered_result,
            context=json.dumps(context),
            where=output_where,
            references=references,
            queryMetadata=query_metadata,
        )

        _trace_output = {
            "role": "ai",
            "content": rendered_result,
            "metadata": {
                "thread_id": f"{thread_id}:{userid}" if userid else thread_id,
                "where": chat_response.where,
                "references": chat_response.references,
                "context": chat_response.context,
                "queryMetadata": query_metadata.model_dump(),
            },
        }
        span.update(output=_trace_output)
        span.set_trace_io(output=_trace_output)

    tenant_id = user.get("tenantId") if user else None
    _track_usage(
        tenant_id,
        "chat",
        llm_total_requests=llm_usage.requests if llm_usage else 0,
        input_tokens=llm_usage.input_tokens if llm_usage else 0,
        output_tokens=llm_usage.output_tokens if llm_usage else 0,
    )

    return chat_response


@router.post(
    "/direct-chat",
    tags=["chat"],
    responses={
        401: {"description": "Unauthorized"},
        400: {"description": "Bad request"},
        403: {"description": "Forbidden"},
        500: {"description": "Internal server error"},
    },
)
@limiter.limit("15/minute")
async def post_direct_chat(
    request: Request,  # noqa: ARG001
    chat_request: models.DirectChatRequestModel,
    user: Annotated[dict[str, Any], Security(require_authentication("direct_chat"))],
) -> models.DirectChatResponseModel:
    """Bypass CSAI processes and communicate directly with LLM.

    This endpoint invokes the LLM directly without engaging the default graph,
    allowing for direct communication with the language model.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from aviator.services.llm import LLMRegistry

    # Convert messages to LangChain format
    langchain_messages = []
    for msg in chat_request.messages:
        content = msg.content
        if msg.author in ["human", "user"]:
            langchain_messages.append(HumanMessage(content=content))
        elif msg.author in ["ai", "assistant"]:
            langchain_messages.append(AIMessage(content=content))
        elif msg.author in ["system", "developer"]:
            langchain_messages.append(SystemMessage(content=content))
        else:
            # Default to HumanMessage for unknown author types
            langchain_messages.append(HumanMessage(content=content))

    try:
        # Get the LLM instance from the registry with the provided options
        llm = LLMRegistry.get_llm(options=chat_request.options or {})

        usage_token = begin_llm_usage_collection()
        llm_usage = None

        try:
            # Invoke the LLM directly
            response = await llm.ainvoke(
                langchain_messages,
                config=RunnableConfig(callbacks=[LLMUsageTrackingCallbackHandler()]),
            )

            result = response.content if hasattr(response, "content") else str(response)
        finally:
            llm_usage = finish_llm_usage_collection(usage_token)

        tenant_id = user.get("tenantId") if user else None
        _track_usage(
            tenant_id,
            "direct_chat",
            llm_total_requests=llm_usage.requests if llm_usage else 0,
            input_tokens=llm_usage.input_tokens if llm_usage else 0,
            output_tokens=llm_usage.output_tokens if llm_usage else 0,
        )

        return models.DirectChatResponseModel(
            result=result,
            chat_id=chat_request.chat_id,
        )

    except Exception as e:
        logger.error("Error in direct chat: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error communicating with LLM: {e!s}",
        ) from e


@router.post(
    "/context",
    tags=["rag"],
    responses={
        401: {"description": "Unauthorized"},
    },
)
async def post_context(
    request: Request,  # noqa: ARG001
    context: models.ContextRequestModel,
    user: Annotated[dict[str, Any], Security(require_authentication("context"))],
) -> models.ContextResposeModel:
    """Search the configured database with the provided query."""

    tenant_id = user.get("tenantId") if user else None

    if settings.dev_tools:
        ### this logging is only for development purpose. it exposes customer data in logs. ###
        logger.debug("Context request received: %s", context)

    documents = await api_rag_query(context=context, tenant_id=tenant_id, user=user)
    _track_usage(tenant_id, "search_query")

    return models.ContextResposeModel(documents=documents)


@router.get(
    "/thread",
    tags=["chat"],
    responses={
        401: {"description": "Unauthorized"},
    },
)
async def get_thread(
    thread_id: str,
    user: Annotated[dict[str, Any], Security(require_authentication("thread"))],
) -> JSONResponse:
    """Retrieve the chat conversation history by its thread id."""

    userid = user.get("login") or user.get("username") or user.get("name") or user.get("email") or user.get("id")

    # Route checkpointer to the tenant schema
    checkpointer_schema.set(tenant_id_to_schema_name(user.get("tenantId")))

    checkpointer = await aviator.get_checkpointer()
    try:
        result = await checkpointer.aget(
            config={
                "configurable": {
                    "thread_id": f"{thread_id}:{userid}" if userid else thread_id,
                },
            }
        )
    except pg_errors.InvalidSchemaName:
        logger.warning("Tenant schema not found when retrieving thread '%s'", thread_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your tenant environment is not yet initialised. Please contact your administrator.",
        ) from None

    return result
