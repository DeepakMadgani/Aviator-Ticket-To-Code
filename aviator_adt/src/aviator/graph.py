"""Module for LangGraph Agent integration with Aviator framework."""

import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from langchain.agents.middleware import ModelResponse
from langchain.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command, RetryPolicy
from langmem.short_term import SummarizationNode
from opentelemetry import trace
from psycopg_pool import AsyncConnectionPool

from aviator.database.checkpointer import TenantAwarePostgresSaver
from aviator.database.pg_client import PgConnectionPool
from aviator.mcp import mcp_client_manager
from aviator.models import GradeModel, StateModel
from aviator.plugins import load_graph_modifiers, load_prompt_modifiers, load_tool_modifiers
from aviator.services.database import DatabaseManager
from aviator.services.llm import LLMRegistry
from aviator.services.prompts_loader import PromptsLoader
from aviator.settings import settings
from aviator.tools.chart_generator import generate_vega_lite_chart
from aviator.tools.date_time import current_time
from aviator.tools.rag import rag_query
from aviator.tools.summarize import generate_summary
from aviator.tools.table_generator import generate_markdown_table
from aviator.utils.citations import extract_citations_from_state

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

OTEL_TRACING_ATTRIBUTES = {"class": "ContentAviatorAgent"}
RAG_HISTORY_STUB_MESSAGE = "Called rag_query for retrieval and got data."

# Substrings used to detect deliberate bias/ethical refusals produced by the assistant.
# When enable_bias_detection is True and the last AI message matches any of these,
# __should_continue routes directly to generate_answer instead of triggering a rewrite loop.
_BIAS_REFUSAL_MARKERS = (
    "i can't help with requests that infer",  # exact prescribed phrase (assistant.md)
    "i can't help with requests",  # shorter prefix of prescribed phrase
    "i cannot fulfill this request",
    "i cannot fulfill",
    "i'm unable to fulfill",
    "i am unable to fulfill",
    "i cannot assist with this request",
    "i cannot assist with",
    "i'm unable to assist with",
    "i cannot process this request",
    "i'm unable to process this request",
)

_INTERNAL_INSTRUCTION_DISCLOSURE_PATTERNS = (
    re.compile(
        r"\b(reveal|show|print|dump|display|list|quote|repeat|tell me|what are|what is)\b"
        r".{0,80}\b("
        r"system prompt|developer prompt|hidden instruction|hidden instructions|"
        r"guardrail|guardrails|citation rule|citation rules|"
        r"internal instruction|internal instructions|internal guardrail|internal guardrails"
        r")\b"
    ),
    # Direct reference to inherently AI-specific system-level terms.
    re.compile(r"\b(system prompt|developer prompt)\b"),
    # Asking the model to ignore its own system/developer instructions.
    re.compile(r"\bignore\b.*\b(previous|above|system|developer)\b.*\b(instruction|instructions|prompt|message)\b"),
)

_INTERNAL_INSTRUCTION_DISCLOSURE_REASON = "internal_instruction_disclosure"
_NO_REFUSAL_REASON = "NONE"
_INTERNAL_INSTRUCTION_REFUSAL_PLACEHOLDER = "Internal instruction disclosure request blocked."


def _is_internal_instruction_disclosure_request(text: str) -> bool:
    """Return True when the user asks to reveal hidden model instructions or configuration."""

    normalized_text = text.lower()
    return any(pattern.search(normalized_text) for pattern in _INTERNAL_INSTRUCTION_DISCLOSURE_PATTERNS)


def _latest_human_message_text(messages: list) -> str:
    """Return the most recent human message text, or an empty string if none exists."""

    human_messages = [msg for msg in messages if isinstance(msg, HumanMessage)]
    return human_messages[-1].text if human_messages else ""


def compact_tool_messages_in_history(
    messages: list,
) -> tuple[list, int]:
    """Compact heavy ToolMessage payloads for selected tools.

    Keeps message chronology intact while replacing bulky content/artifact payloads
    with a lightweight stub.

    Returns:
        Tuple of (possibly updated messages, count_of_compacted_messages)

    """
    tool_names_to_compact = ["rag_query"]

    compacted_count = 0
    compacted_messages = []

    for msg in messages:
        if isinstance(msg, ToolMessage) and getattr(msg, "name", None) in tool_names_to_compact:
            updates: dict[str, Any] = {}
            if msg.content != RAG_HISTORY_STUB_MESSAGE:
                updates["content"] = RAG_HISTORY_STUB_MESSAGE
            if getattr(msg, "artifact", None) is not None:
                updates["artifact"] = None

            if updates:
                msg = msg.model_copy(update=updates)
                compacted_count += 1

        compacted_messages.append(msg)

    return compacted_messages, compacted_count


class ContentAviatorAgent:
    """LangGraph Agent integrated with Aviator framework."""

    llm: BaseChatModel
    llm_assistant: BaseChatModel
    prompts: PromptsLoader
    _connection_pool: AsyncConnectionPool | None = None
    _checkpointer: TenantAwarePostgresSaver | InMemorySaver | None = None
    _graph: CompiledStateGraph | None = None
    _tools: list[BaseTool] | None = None
    _summarization_node: SummarizationNode | None = None

    def __init__(self) -> None:
        """Initialize the LangGraph Agent with necessary components."""
        # Use the LLM service with tools bound

        self.llm = LLMRegistry.get_llm()
        self.llm_assistant = LLMRegistry.get_llm(assistant=True)
        self.prompts = PromptsLoader(model_provider=settings.llm_provider, model=settings.llm_model)

        # Initialize summarization node if enabled
        if settings.message_history_enabled and settings.message_history_strategy == "summarization":
            # Create LLM instance for summarization with limited output tokens
            summarization_model = LLMRegistry.get_llm(
                options={"max_tokens": settings.message_history_max_summary_tokens}
            )
            self._summarization_node = SummarizationNode(
                token_counter=count_tokens_approximately,
                model=summarization_model,
                max_tokens=settings.message_history_max_summary_tokens,
                max_tokens_before_summary=settings.message_history_max_tokens,
                max_summary_tokens=int(settings.message_history_max_summary_tokens * 0.9),
                output_messages_key="llm_input_messages",
            )

    @staticmethod
    def _current_timestamp_utc() -> str:
        """Return current UTC timestamp in ISO-8601 format with second precision."""

        return datetime.now(tz=UTC).isoformat(timespec="seconds")

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="_get_connection_pool")
    async def _get_connection_pool(self) -> AsyncConnectionPool:
        """Get the shared PostgreSQL async connection pool.

        Returns:
            AsyncConnectionPool: A connection pool for PostgreSQL database.

        """
        if self._connection_pool is None:
            try:
                self._connection_pool = await PgConnectionPool.get_pool()
                logger.info("connection_pool_acquired -> shared PgConnectionPool")
            except Exception as e:
                logger.error("connection_pool_creation_failed -> %s", e)
                raise

        return self._connection_pool

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="get_checkpointer")
    async def get_checkpointer(self) -> TenantAwarePostgresSaver | InMemorySaver | None:
        """Get or create the checkpointer for the graph.

        Returns:
            TenantAwarePostgresSaver | InMemorySaver | None: The checkpointer instance or None if not configured.

        """
        if self._checkpointer is None:
            match settings.checkpointer:
                # Determine checkpointer type from settings
                case "memory":
                    self._checkpointer = InMemorySaver()

                case "postgres":
                    # Get connection pool for checkpointer
                    connection_pool = await DatabaseManager.get_pool()
                    if connection_pool:
                        self._checkpointer = TenantAwarePostgresSaver(
                            connection_pool, serde=JsonPlusSerializer(pickle_fallback=True)
                        )
                        await self._checkpointer.setup()
                    else:
                        self._checkpointer = None

                case _:
                    self._checkpointer = None

        return self._checkpointer

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="get_tools")
    async def get_tools(self, state: StateModel | None = None, config: RunnableConfig | None = None) -> list[BaseTool]:
        """Get the list of graph tools, including MCP tools for assistant scope."""

        # Extract user context for tenant identification and auth (same pattern as rag.py)
        # Prefer state.user (more direct) over config.configurable
        user_context = None
        if state and state.user:
            user_context = state.user

        if user_context:
            # Inject OTDS token into MCP strategies if available
            self._inject_mcp_token(user_context)

        # Always start with built-in tools
        assistant_tools = [
            current_time,
            rag_query,
            generate_summary,
            generate_vega_lite_chart,
            generate_markdown_table,
        ]

        # Only load MCP tools when we have tenant context (during actual request execution)
        # Skip during graph initialization (ToolNode creation) when user_context is None
        if user_context:
            mcp_tools = await mcp_client_manager.get_assistant_tools(user=user_context)
            assistant_tools += mcp_tools
            logger.debug("Loaded %d MCP tools for tenant", len(mcp_tools))
        else:
            logger.debug("Skipping MCP tools - no tenant context (graph initialization)")

        # PLUGIN system - tool modifiers can still modify the final list
        modifiers = load_tool_modifiers()
        for modify in modifiers:
            modify(tools=assistant_tools, state=state, config=config)

        return assistant_tools

    def _inject_mcp_token(self, user_context: dict[str, Any]) -> None:
        """Inject user token into MCP OTDS token auth strategies.

        Args:
            user_context: User context from RunnableConfig containing auth info
                         Token always comes in 'authorization' field as Bearer token

        """
        if not user_context:
            return

        # Extract Bearer token from Authorization header
        auth_header = user_context.get("authorization")

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix to get raw token
            try:
                # Inject token into all MCP servers with OTDS token auth (tenant-aware via user_context)
                mcp_client_manager.inject_otds_token(token, user=user_context)
                logger.debug("Injected user Bearer token into MCP OTDS token auth strategies")
            except Exception as e:
                logger.error("Failed to inject MCP token: %s", e)
        else:
            logger.debug("No Bearer token found in user context for MCP OTDS token auth")

    async def get_plugin_tools(self) -> list[BaseTool]:
        """Get MCP tools specifically available to plugin custom nodes.

        Returns:
            List of MCP tools configured for custom/plugin scope

        """
        return await mcp_client_manager.get_plugin_mcp_tools()

    async def get_graph(self) -> CompiledStateGraph:
        """Get or Create and configure the LangGraph.

        Returns:
            CompiledStateGraph: The configured LangGraph instance or None if init fails

        """
        if self._graph is None:
            try:
                start_time = time.perf_counter()

                logger.info("Creating state graph...")

                graph_builder = StateGraph(StateModel)

                # Define nodes: these do the work
                graph_builder.add_node("init", self.__init)
                graph_builder.add_node("manage_message_history", self.__manage_message_history)
                graph_builder.add_node("assistant", self.__assistant)
                graph_builder.add_node("tool_node", self.__dynamic_tool_node, retry_policy=RetryPolicy(max_attempts=3))
                graph_builder.add_node("rewrite_question", self.__rewrite_question)
                graph_builder.add_node("format_answer", self.__format_answer)
                graph_builder.add_node("extract_references", self.__extract_references)

                # Define edges: these determine how the control flow moves
                graph_builder.set_entry_point("init")
                graph_builder.add_edge(START, "manage_message_history")
                graph_builder.add_edge("manage_message_history", "assistant")
                graph_builder.add_edge("init", "assistant")
                graph_builder.add_conditional_edges(
                    "assistant",
                    self.__should_continue,
                    {
                        "execute_tools": "tool_node",
                        "rewrite_question": "rewrite_question",
                        "generate_answer": "extract_references",
                        "format_summary": "format_answer",
                    },
                )
                graph_builder.add_edge("tool_node", "assistant")
                graph_builder.add_edge("rewrite_question", "assistant")
                graph_builder.add_edge("extract_references", "format_answer")
                graph_builder.add_edge("format_answer", END)

                # Get or create checkpointer
                if self._checkpointer is None:
                    self._checkpointer = await self.get_checkpointer()

                # PLUGIN system
                modifiers = load_graph_modifiers()
                for modify in modifiers:
                    modify(graph_builder)

                self._graph = graph_builder.compile(checkpointer=self._checkpointer, name="Content Aviator Agent")

                logger.info("graph_created Content Aviator Agent")

                elapsed = time.perf_counter() - start_time
                logger.debug("building state graph took %.2f seconds", elapsed)

            except Exception as e:
                logger.error("graph_creation_failed -> str(%s)", e)
                raise

        return self._graph

    ###
    # Graph Nodes
    ###

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="__manage_message_history")
    async def __manage_message_history(self, state: StateModel, config: RunnableConfig | None = None) -> dict[str, Any]:
        """Manage message history using either summarization or trimming.

        This node runs before the assistant node and manages the conversation history
        to ensure it stays within LLM context limits, on every turn.
        First compacts configured tool messages to reduce size, then applies history management.

        Returns llm_input_messages: summary + recent messages (sent to LLM)
        Also replaces state.messages with trimmed/summarized version to prevent unbounded growth.
        """

        if not settings.message_history_enabled:
            # No management needed - return empty dict (no state updates)
            return {}

        messages_to_manage, compacted_count = compact_tool_messages_in_history(
            state.messages,
        )

        compact_only_update: dict[str, Any] = {}
        if compacted_count > 0:
            logger.info("Compacted %d tool messages from history", compacted_count)
            compact_only_update = {
                "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages_to_manage],
            }

        current_tokens = count_tokens_approximately(messages_to_manage)
        if current_tokens <= settings.message_history_max_tokens:
            # Under limit - no additional management needed.
            return compact_only_update

        try:
            writer = get_stream_writer()
        except RuntimeError:
            writer = logger.info

        try:
            if settings.message_history_strategy == "summarization":
                if self._summarization_node:
                    logger.info("Summarizing message history (current tokens: %d)...", current_tokens)
                    writer({"event": "message", "message": "Summarizing conversation history...\n"})
                    human_indices = [
                        i for i, message in enumerate(messages_to_manage) if isinstance(message, HumanMessage)
                    ]
                    if len(human_indices) < 2:
                        logger.info("Only one conversation turn found; skipping summarization")
                        return compact_only_update

                    keep_from_idx = human_indices[-2]
                    messages_to_keep = messages_to_manage[keep_from_idx:]
                    messages_to_summarize = messages_to_manage[:keep_from_idx]

                    if not messages_to_summarize:
                        logger.info("No prior turns to summarize; keeping current turn messages unchanged")
                        return compact_only_update

                    result = await self._summarization_node.ainvoke(
                        {"messages": messages_to_summarize, "context": state.summarization_context},
                        config=config,
                    )
                    logger.info(
                        "Summarization completed. Updated context length: %d",
                        len(result.get("context", state.summarization_context)) if result.get("context") else 0,
                    )

                    logger.debug("Summarization result keys: %s", result.keys())
                    llm_input = result.get("llm_input_messages", messages_to_manage)
                    # Normalize message content structure - ensure all messages have proper content
                    # SummarizationNode may create messages with improper content structure
                    normalized_messages = []
                    for msg in llm_input:
                        if (
                            hasattr(msg, "content")
                            and isinstance(msg.content, list)
                            and any(isinstance(item, str) for item in msg.content)
                        ):
                            # Flatten to simple string content
                            logger.warning("Normalizing message content from list with strings to plain string")
                            msg.content = "".join(
                                item
                                if isinstance(item, str)
                                else item.get("text", "")
                                if isinstance(item, dict)
                                else ""
                                for item in msg.content
                            )
                        normalized_messages.append(msg)

                    merged_messages = [*normalized_messages, *messages_to_keep]

                    logger.info(
                        "Normalized %d summarized messages and kept %d latest-turn messages",
                        len(normalized_messages),
                        len(messages_to_keep),
                    )

                    summarized_count = len(messages_to_manage) - len(merged_messages)
                    if summarized_count > 0:
                        writer(
                            {
                                "event": "message",
                                "message": f"Summarized {summarized_count} messages from history\n",
                            }
                        )
                    return {
                        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *merged_messages],
                        "summarization_context": result.get("context", state.summarization_context),
                    }
                else:
                    logger.error("Summarization node not initialized but summarization strategy is configured")
                    # Return empty - keep existing messages, no state updates
                    return compact_only_update

            elif settings.message_history_strategy == "trimming":
                # Trimming strategy explicitly configured
                writer({"event": "message", "message": "Trimming conversation history...\n"})

                trimmed_messages = trim_messages(
                    messages_to_manage,
                    strategy="last",
                    token_counter=count_tokens_approximately,
                    max_tokens=settings.message_history_max_tokens,
                    start_on="human",
                    end_on=("human", "tool"),
                )

                trimmed_count = len(messages_to_manage) - len(trimmed_messages)
                if trimmed_count > 0:
                    writer({"event": "message", "message": f"Trimmed {trimmed_count} messages from history\n"})

                return {
                    "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *trimmed_messages],
                }
            else:
                logger.warning("Unknown message history strategy: %s", settings.message_history_strategy)
                return {}

        except Exception:
            logger.exception("Message history management failed")
            # Fallback: no state updates on error, keep existing messages
            return {}

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="__init")
    async def __init(self, state: StateModel) -> dict:
        """Define init Node.

        !!! info
            Only called once at the beginning of the graph execution. Used to reset counters and store initial question.
        """

        query = state.messages[-1].text

        return {"rewrite_counter": 0, "references": [], "query": query, "refusal_reason": None}

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="__assistant")
    async def __assistant(self, state: StateModel, config: RunnableConfig) -> dict:
        """Define the function that calls the model.

        !!! Prompt
            ```markdown
            --8<-- "src/aviator/prompts/assistant.md"
            ```
        """

        writer = get_stream_writer()
        writer({"event": "message", "message": "Thinking ...\n"})

        latest_user_query = _latest_human_message_text(state.messages)
        if latest_user_query and _is_internal_instruction_disclosure_request(latest_user_query):
            logger.info("Blocking internal-instruction disclosure request")
            return {
                # The final user-visible refusal is produced later in format_answer so it can
                # follow the same language behavior as every other response.
                "messages": AIMessage(content=_INTERNAL_INSTRUCTION_REFUSAL_PLACEHOLDER),
                "refusal_reason": _INTERNAL_INSTRUCTION_DISCLOSURE_REASON,
            }

        # Dynamically read the assistant prompt from the markdown file asynchronously
        assistant_prompt_text = await self.prompts.load_prompt(
            prompt_name="assistant", model=settings.llm_model_assistant
        )

        if not settings.enable_bias_detection:
            assistant_prompt_text = re.sub(
                r"^## Ethical Guardrail.*?(?=^## |\Z)",
                "",
                assistant_prompt_text,
                flags=re.DOTALL | re.MULTILINE,
            )

        primary_assistant_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", assistant_prompt_text),
                ("human", "The list of documents or workspaces in context are: {where}"),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        load_prompt_modifiers(
            prompt_name="primary_assistant_prompt", template=primary_assistant_prompt, state=state, config=config
        )
        assistant_runnable = primary_assistant_prompt | self.llm_assistant.bind_tools(
            await self.get_tools(state=state, config=config),
        )

        response = await assistant_runnable.ainvoke(
            {
                "messages": state.messages,
                "where": [item.model_dump(by_alias=True, exclude_none=True) for item in state.where],
                "current_timestamp": self._current_timestamp_utc(),
            },
            config=config,
        )

        # settings, substitute a user-friendly refusal message instead of an empty response.
        if response.response_metadata.get("finish_reason") == "SAFETY":
            logger.warning(
                "LLM blocked response due to safety settings (finish_reason=SAFETY). "
                "Replacing with safety refusal message. safety_ratings=%s",
                response.response_metadata.get("safety_ratings"),
            )
            response = response.model_copy(
                update={
                    "content": (
                        "Sorry, it seems there was an issue with generating a response to your query. "
                        "Our priority is to maintain a safe and positive environment for all users, "
                        "and occasionally our safety filters may restrict certain content. "
                        "Please feel free to rephrase your question, and I'll do my best to assist you "
                        "within our guidelines. Thank you for your understanding."
                    )
                }
            )

        state_update = {"messages": response}

        # We return a list, because this will get added to the existing list
        return state_update

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="__should_continue")
    async def __should_continue(
        self, state: StateModel, config: RunnableConfig
    ) -> Literal["execute_tools", "generate_answer", "rewrite_question", "format_summary"]:
        """Define the function that determines whether to continue or not.

        !!! Prompt
            ```markdown
            --8<-- "src/aviator/prompts/grader.md"
            ```
        """

        rewrite_question_prompt_text = await self.prompts.load_prompt("grader")

        grade_prompt = ChatPromptTemplate.from_messages([("human", rewrite_question_prompt_text)])

        load_prompt_modifiers(prompt_name="grade_prompt", template=grade_prompt, state=state, config=config)
        grade_runnable = grade_prompt | self.llm.with_structured_output(GradeModel)

        last_message = state.messages[-1]
        summary_used_in_current_turn = False

        for msg in reversed(state.messages):
            if isinstance(msg, HumanMessage):
                break
            if isinstance(msg, ToolMessage):
                summary_used_in_current_turn = getattr(msg, "name", None) == "generate_summary"
                break

        # Check if additional Tool calls are required

        if not last_message.tool_calls:
            if summary_used_in_current_turn:
                return "format_summary"

            # Evaluate the response and rewrite the query if needed
            question = _latest_human_message_text(state.messages)

            ai_messages = [msg for msg in state.messages if isinstance(msg, AIMessage)]
            context = ai_messages[-1].text if ai_messages else ""

            if state.refusal_reason == _INTERNAL_INSTRUCTION_DISCLOSURE_REASON:
                logger.debug("Internal-instruction refusal detected; routing directly to generate_answer")
                return "generate_answer"

            # When bias detection is enabled and the assistant issued a
            # deliberate refusal, treat the response as a fully-answered terminal state.
            # Without this, the grader scores a refusal as "no answer found" and routes to
            # rewrite_question.
            if settings.enable_bias_detection:
                last_text_lower = context.lower()
                if any(marker in last_text_lower for marker in _BIAS_REFUSAL_MARKERS):
                    logger.debug("Bias refusal detected in assistant response; routing directly to generate_answer")
                    return "generate_answer"

            try:
                response = await grade_runnable.ainvoke(
                    {
                        "question": question,
                        "context": context,
                    },
                    config=config,
                )
                score = response.binary_score
            except Exception as e:
                # Fallback for providers that don't support structured output
                logger.warning("Grader structured output failed (unsupported provider): %s", e)
                # Default to generating answer if grading fails
                return "generate_answer"

            # Only attempt x times to rewrite the question.
            if score == "yes" or state.rewrite_counter >= 1:
                return "generate_answer"
            else:
                return "rewrite_question"
        else:
            return "execute_tools"

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="__rewrite_question")
    async def __rewrite_question(self, state: StateModel, config: RunnableConfig) -> dict:
        """Rewrite the original user question.

        !!! Prompt
            ```markdown
            --8<-- "src/aviator/prompts/rewrite_question.md"
            ```
        """

        writer = get_stream_writer()
        writer({"event": "message", "message": "Rephrasing question ...\n"})

        # Dynamically read the assistant prompt from the markdown file asynchronously
        rewrite_question_prompt_text = await self.prompts.load_prompt("rewrite_question")

        rewrite_prompt = ChatPromptTemplate.from_messages([("human", rewrite_question_prompt_text)])

        load_prompt_modifiers(prompt_name="rewrite_prompt", template=rewrite_prompt, state=state, config=config)
        rewrite_runnable = rewrite_prompt | self.llm

        human_messages = [msg for msg in state.messages if isinstance(msg, HumanMessage)]
        question = human_messages[-1].text
        response = await rewrite_runnable.ainvoke(
            {
                "question": question,
                "current_timestamp": self._current_timestamp_utc(),
            },
            config=config,
        )

        state.rewrite_counter += 1
        rewrite_counter = state.rewrite_counter

        # Rewrite the Question and inject it as HumanMessage
        return {
            "messages": [HumanMessage(content=response.text)],
            "rewrite_counter": rewrite_counter,
        }

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="__format_answer")
    async def __format_answer(self, state: StateModel, config: RunnableConfig) -> dict:
        """Format the answer for better readability.

        !!! Prompt
            ```markdown
            --8<-- "src/aviator/prompts/answer_and_references.md"
            ```
        """

        writer = get_stream_writer()
        writer({"event": "message", "message": "Formatting answer ...\n"})

        if not state.messages[-1].text:
            state.messages[-1] = AIMessage(
                content="Sorry I wasn't able to provide an answer to the question. Please rephrase it and try again."
            )

        # Dynamically read the formatting prompt from the markdown file asynchronously
        answer_and_references_prompt_text = await self.prompts.load_prompt("answer_and_references")

        # Determine citation status from state
        citation_status = "ENABLED" if state.inlineCitation else "DISABLED"

        # Simple formatting prompt without structured output for streaming support
        format_prompt = ChatPromptTemplate.from_messages(
            [
                ("human", answer_and_references_prompt_text),
            ]
        )
        load_prompt_modifiers(prompt_name="format_prompt", template=format_prompt, state=state, config=config)
        format_runnable = format_prompt | self.llm

        response = await format_runnable.ainvoke(
            {
                "input": state.messages[-1].text,
                "question": state.query or "",
                "inline_citations_enabled": citation_status,
                "refusal_reason": state.refusal_reason or _NO_REFUSAL_REASON,
            },
            config=config,
        )

        formatted_content = response.text or state.messages[-1].text

        # Only append the disclaimer when the summary tool was used in this specific
        # turn, to avoid stale state leaking into subsequent turns via the checkpointer.
        summary_used_in_current_turn = False
        for msg in reversed(state.messages):
            if isinstance(msg, HumanMessage):
                break
            if isinstance(msg, ToolMessage):
                summary_used_in_current_turn = getattr(msg, "name", None) == "generate_summary"
                break

        if summary_used_in_current_turn and state.summary_disclaimer:
            formatted_content = formatted_content + "\n\n" + state.summary_disclaimer

        formatted_message = AIMessage(content=formatted_content)

        result = {"messages": formatted_message}

        # Use references from state if set by agent
        if state.references:
            result["references"] = state.references

        return result

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="__extract_references")
    async def __extract_references(self, state: StateModel) -> dict:
        """Extract inline citations from the answer and build references.

        This node runs before format_answer and extracts [CHUNK_ID] citations
        from the assistant's response. It looks up those chunk IDs in the tool messages
        (RAG results) and builds proper ReferenceModel objects.
        """
        writer = get_stream_writer()
        writer({"event": "message", "message": "Extracting inline citations ...\n"})

        # Extract inline citations from state
        references = extract_citations_from_state(state)

        if references:
            writer(
                {
                    "event": "message",
                    "message": f"Found {sum(len(ref.chunks) for ref in references)} citations across {len(references)} documents.\n",
                }
            )
            return {"references": references}

        return {}

    async def __dynamic_tool_node(self, state: StateModel, config: RunnableConfig) -> dict:
        """Execute tool calls using dynamically loaded tools, including per-tenant MCP tools."""
        tools = await self.get_tools(state=state, config=config)
        node = ToolNode(tools, awrap_tool_call=self.tool_wrapper)
        return await node.ainvoke(state, config)

    @tracer.start_as_current_span(attributes=OTEL_TRACING_ATTRIBUTES, name="tool_wrapper")
    async def tool_wrapper(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ModelResponse:
        """Define middleware that streams tool call events."""

        writer = get_stream_writer()

        # Initial log + streaming message
        args = request.tool_call.get("args")
        msg = f"Executing -> '{request.tool_call.get('name')}'"
        if args:
            msg += f" with arguments -> {args}"
        msg += "..."
        logger.debug(msg)
        writer({"event": "message", "message": msg})

        # Collect execution timing:
        start_time = datetime.now(UTC)

        # Call the actual tool handler
        result = await handler(request)

        end_time = datetime.now(UTC)
        duration = end_time - start_time
        seconds = duration.total_seconds()

        msg = f"Execution -> '{request.tool_call.get('name')}' finished after {seconds:.2f}s."
        logger.debug("%s. Result -> %s", msg, str(result))
        writer({"event": "message", "message": msg})

        return result


aviator = ContentAviatorAgent()


async def create_state_graph() -> CompiledStateGraph:
    """Create and return the state graph for LangGraph Studio.

    Returns:
        CompiledStateGraph: The configured LangGraph instance

    """
    return await aviator.get_graph()
