"""A2A Pydantic models and JSON-RPC 2.0 constants.

All data-shapes specific to the A2A / JSON-RPC 2.0 protocol live here.
General shared models (ChatMessageModel, DocumentReferenceModel …) are
imported from ``aviator.models`` to avoid duplication.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aviator.models import (  # noqa: TC001
    ChatMessageModel,
    WhereClauseReferenceModel,
)

logger = logging.getLogger(__name__)

# ── JSON-RPC 2.0 error codes ──────────────────────────────────────────────────
# Standard codes
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_SERVER_ERROR = -32000  # -32000 to -32099 reserved for implementation errors

# A2A-specific application error codes
JSONRPC_TASK_NOT_FOUND = -32001  # No task with the given ID
JSONRPC_TASK_NOT_CANCELABLE = -32002  # Task is in a terminal state
JSONRPC_UNSUPPORTED_OPERATION = -32004  # Operation not supported by this agent
JSONRPC_CONTENT_TYPE_NOT_SUPPORTED = -32005  # Input content type not accepted


# ── A2A Methods ───────────────────────────────────────────────────────────────

# MIME type constants
CONTENT_TYPE_SSE = "text/event-stream"  # Server-Sent Events


class A2AMethod(StrEnum):
    """Enumeration of supported A2A protocol methods."""

    MESSAGE_SEND = "message/send"
    MESSAGE_STREAM = "message/stream"
    TASKS_GET = "tasks/get"
    TASKS_CANCEL = "tasks/cancel"


# Message methods that require A2AAgentInvokeRequest validation
MESSAGE_METHODS: frozenset[str] = frozenset({A2AMethod.MESSAGE_SEND, A2AMethod.MESSAGE_STREAM})


# ── Skill ─────────────────────────────────────────────────────────────────────


class Skill(BaseModel):
    """A skill or tool capability exposed by the agent."""

    id: str = Field(..., description="Unique identifier for the skill")
    name: str = Field(..., description="Human-readable name of the skill")
    description: str = Field(..., description="Detailed description of what the skill does")
    inputModes: list[str] = Field(default=["text/plain"], description="Accepted input MIME types")
    outputModes: list[str] = Field(default=["text/plain"], description="Produced output MIME types")
    examples: list[str] = Field(default=[], description="Example prompts that trigger this skill")
    tags: list[str] = Field(default=[], description="Tags for categorising and discovering the skill")


# ── Agent Invocation ──────────────────────────────────────────────────────────


class A2AAgentInvokeRequest(BaseModel):
    """A2A-compliant request for invoking the agent."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "message": {
                        "role": "user",
                        "parts": [{"text": "What information do you have about X?"}],
                        "contextId": "thread-uuid",
                        "metadata": {"where": [{"workspaceID": "67890"}]},
                    }
                }
            ]
        },
    )

    context: str | None = None
    inlineCitation: bool = True
    messages: list[ChatMessageModel]
    where: list[WhereClauseReferenceModel] = Field(
        default_factory=list,
        description="Context restrictions (grounding) on workspaces and documents",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_input(cls, values: dict) -> dict:
        r"""Normalise A2A v1.0 standard format (``params.message``) → internal format.

        **Standard A2A v1.0** (``params.message`` — single Message object):

        .. code-block:: json

            {
              "message": {
                "role": "user",
                "parts": [{ "text": "Summarise workspace" }],
                "contextId": "thread-uuid",
                "metadata": { "where": [...] }
              }
            }

        **Role mapping:**
            ``role \"user\"``  → ``author \"user\"``
            ``role \"agent\"`` → ``author \"assistant\"``
        """
        msg = values.pop("message", None)
        if msg is None:
            err = "message field is required"
            raise ValueError(err)

        if not isinstance(msg, dict):
            err = "params.message must be a JSON object"
            raise TypeError(err)

        # Validate required fields
        role = msg.get("role")
        if not role:
            err = "message.role is required"
            raise ValueError(err)

        parts = msg.get("parts")
        if not parts or not isinstance(parts, list):
            err = "message.parts is required and must be a non-empty array"
            raise ValueError(err)

        text_parts = [p for p in parts if isinstance(p, dict) and "text" in p]
        if not text_parts:
            err = "message.parts must contain at least one text part"
            raise ValueError(err)

        if not any(p["text"].strip() for p in text_parts if isinstance(p.get("text"), str)):
            err = "parts[].text must not be empty"
            raise ValueError(err)

        unsupported = [p for p in parts if isinstance(p, dict) and "text" not in p]
        if unsupported:
            part_kinds = [next((k for k in ("data", "file") if k in p), "unknown") for p in unsupported]
            logger.info(
                "normalize_input: %d non-text part(s) ignored (kinds: %s) — content will be empty if no text parts present",
                len(unsupported),
                part_kinds,
            )
        content = " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p).strip()

        # contextId → internal context JSON (LangGraph thread_id)
        # Always generate a UUID if the client omits contextId — ensures every task
        # has a stable thread identity for LangGraph and multi-turn follow-ups.
        context_id = msg.get("contextId") or str(uuid4())
        context_payload: dict = {"thread_id": context_id}

        # referenceTaskIds — carry through for dependent/chained task scenarios
        reference_task_ids = msg.get("referenceTaskIds") or []
        if reference_task_ids:
            context_payload["reference_task_ids"] = reference_task_ids

        values["context"] = json.dumps(context_payload)

        # Optional extensions carried in message.metadata
        metadata = msg.get("metadata") or {}
        if metadata.get("where"):
            values["where"] = metadata["where"]

        # role → author mapping
        _role_to_author = {"user": "user", "agent": "assistant"}
        author = _role_to_author.get(role, "user")
        values["messages"] = [{"author": author, "content": content}]
        return values


class A2AAgentInvokeResponse(BaseModel):
    """A2A-compliant response from an agent invocation."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "result": "Here is the information about X...",
                    "context": "{}",
                    "where": [{"documentID": "12345"}],
                    "references": [{"documentID": "12345", "workspaceID": "67890", "chunks": []}],
                }
            ]
        },
    )

    result: str | None = Field(default=None, description="The response text from the agent")
    context: str | None = None
    where: list[WhereClauseReferenceModel] = Field(
        default_factory=list,
        description="Context restrictions that were applied",
    )
    references: list[dict] = Field(
        default_factory=list,
        description="References and citations from the result",
    )


# ── A2A Task lifecycle — spec-compliant types ────────────────────────────────


class TaskState(StrEnum):
    """Lifecycle states for an A2A task (A2A spec §3.3)."""

    SUBMITTED = "submitted"  # Received, not yet started
    WORKING = "working"  # Graph is executing
    INPUT_REQUIRED = "input-required"  # LangGraph interrupt — awaiting user input
    COMPLETED = "completed"  # Finished successfully
    CANCELED = "canceled"  # Cancelled by client
    FAILED = "failed"  # Unrecoverable error
    REJECTED = "rejected"  # Agent refused the request
    AUTH_REQUIRED = "auth-required"  # Agent needs additional auth
    UNKNOWN = "unknown"  # State cannot be determined


class Role(StrEnum):
    """A2A message role — identifies the sender of a message (A2A spec §3.1)."""

    USER = "user"  # Message from the human / client
    AGENT = "agent"  # Message from the AI agent


# ── Part types (content atoms inside messages and artifacts) ──────────────────


# ── Part types (A2A v1.0: member name as discriminator) ───────────────


class TextPart(BaseModel):
    """A plain-text content part (A2A v1.0 pattern)."""

    text: str = Field(..., description="The text content")
    metadata: dict | None = Field(default=None, description="Optional part metadata")


class DataPart(BaseModel):
    """A structured-data content part (A2A v1.0 pattern)."""

    data: dict = Field(..., description="Arbitrary JSON-serialisable data")
    mediaType: str = Field(default="application/json", description="MIME type of the data")
    metadata: dict | None = Field(default=None, description="Optional part metadata")


class FilePart(BaseModel):
    """A file content part (A2A v1.0 pattern)."""

    raw: str | None = Field(default=None, description="Base64-encoded file bytes (if inline)")
    url: str | None = Field(default=None, description="URL to the file (if remote)")
    filename: str = Field(..., description="File name")
    mediaType: str = Field(..., description="MIME type of the file")
    metadata: dict | None = Field(default=None, description="Optional part metadata")


# Union type for all supported part variants (A2A v1.0: member name as discriminator)
Part = Annotated[
    TextPart | DataPart | FilePart, Field(discriminator=None)  # No discriminator, member name is type
]


# ── Message ───────────────────────────────────────────────────────────────────


class A2AMessage(BaseModel):
    """A spec-compliant A2A message with typed parts (A2A v1.0 pattern)."""

    role: Role
    parts: list[Part] = Field(..., description="Content parts")
    messageId: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique message identifier",
    )
    taskId: str | None = Field(default=None, description="Parent task ID")
    contextId: str | None = Field(default=None, description="Conversation context ID (thread)")
    referenceTaskIds: list[str] = Field(
        default_factory=list,
        description="IDs of prior tasks this message depends on (task chaining)",
    )
    metadata: dict | None = Field(default=None, description="Optional message metadata")


# ── Artifact ──────────────────────────────────────────────────────────────────


class Artifact(BaseModel):
    """Structured output produced by an agent task (A2A v1.0 pattern)."""

    artifactId: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique artifact identifier",
    )
    name: str | None = Field(default=None, description="Human-readable artifact name")
    parts: list[Part] = Field(..., description="Content parts")
    metadata: dict | None = Field(default=None, description="Optional artifact metadata")


# ── Task & status ─────────────────────────────────────────────────────────────


class TaskStatus(BaseModel):
    """The current lifecycle state of a task."""

    state: TaskState
    message: A2AMessage | None = Field(default=None, description="Optional status message from agent")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 timestamp of this status",
    )


class Task(BaseModel):
    """A2A task — the core unit of work returned by message/send and message/stream."""

    kind: Literal["task"] = "task"
    id: str
    contextId: str | None = Field(default=None, description="Conversation context / thread ID")
    status: TaskStatus
    artifacts: list[Artifact] = Field(default_factory=list, description="Outputs produced so far")
    history: list[A2AMessage] = Field(default_factory=list, description="Message history (if enabled)")
    metadata: dict | None = Field(default=None, description="Optional task metadata")


# ── SSE streaming events ──────────────────────────────────────────────────────


# ── SSE streaming events (A2A v1.0: wrapper member as discriminator) ─────────


class TaskStatusUpdate(BaseModel):
    """Typed payload carried inside a ``TaskStatusUpdateEvent``."""

    taskId: str
    contextId: str | None = None
    status: TaskStatus
    final: bool = False
    progressLabel: str | None = None


class TaskArtifactUpdate(BaseModel):
    """Typed payload carried inside a ``TaskArtifactUpdateEvent``."""

    taskId: str
    contextId: str | None = None
    artifact: Artifact


class TaskStatusUpdateEvent(BaseModel):
    """SSE event emitted when a task's state changes (A2A v1.0 pattern)."""

    statusUpdate: TaskStatusUpdate


class TaskArtifactUpdateEvent(BaseModel):
    """SSE event emitted when a task produces or updates an artifact (A2A v1.0 pattern)."""

    artifactUpdate: TaskArtifactUpdate


# ── JSON-RPC 2.0 envelope ─────────────────────────────────────────────────────


class JsonRpcRequest(BaseModel):
    """JSON-RPC 2.0 request envelope."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "jsonrpc": "2.0",
                    "id": "req-123",
                    "method": "message/send",
                    "params": {
                        "context": "{}",
                        "inlineCitation": True,
                        "messages": [{"role": "user", "content": "What is your Name?"}],
                        "where": [{"documentID": "12345"}],
                    },
                }
            ]
        }
    )

    jsonrpc: Literal["2.0"] = Field(default="2.0", description="JSON-RPC version, must be '2.0'")
    id: str | int | None = Field(default=None, description="Request identifier echoed in response")
    method: str = Field(..., description="Method to invoke, e.g. 'message/send'")
    params: dict | None = Field(default=None, description="Method parameters")


class JsonRpcError(BaseModel):
    """JSON-RPC 2.0 error object."""

    code: int = Field(..., description="Error code")
    message: str = Field(..., description="Short error description")
    data: dict | None = Field(default=None, description="Additional error details")


class JsonRpcResponse(BaseModel):
    """JSON-RPC 2.0 response envelope."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "jsonrpc": "2.0",
                    "id": "req-123",
                    "result": {
                        "result": "I am Content Aviator...",
                        "context": '{"thread_id": "abc-123"}',
                        "where": [],
                        "references": [],
                    },
                }
            ]
        }
    )

    jsonrpc: Literal["2.0"] = Field(default="2.0", description="JSON-RPC version")
    id: str | int | None = Field(default=None, description="Echoed request identifier")
    result: dict | None = Field(default=None, description="Successful result payload")
    error: JsonRpcError | None = Field(default=None, description="Error object, mutually exclusive with result")


# ── Agent Card ────────────────────────────────────────────────────────────────


class SupportedInterface(BaseModel):
    """A protocol interface supported by this agent."""

    url: str
    protocolBinding: str  # "JSON-RPC" | "HTTP+JSON"
    protocolVersion: str  # "1.0"


class SecurityScheme(BaseModel):
    """Security scheme descriptor.

    Supports the types that reflect the pluggable ``require_authentication()`` handler:

    * ``http``    — HTTP Bearer token (``Authorization: Bearer <token>``).  The
                    client is responsible for obtaining a valid token from OTDS
                    (or any other issuer).  Use ``scheme="bearer"``.
    * ``apiKey``  — OTCS ticket passed in a custom header (``otcsticket: <ticket>``).

    Fields are optional so the same model covers both variants without subclassing.
    """

    type: str  # "http" | "apiKey"
    description: str | None = None

    # ── http fields ──
    scheme: str | None = None  # e.g. "bearer"
    bearerFormat: str | None = None  # e.g. "JWT" (informational)

    # ── apiKey fields ──
    name: str | None = None  # header / query param name
    location: str | None = Field(default=None, alias="in")  # "header" | "query" | "cookie"

    model_config = ConfigDict(populate_by_name=True)


class AgentCapabilities(BaseModel):
    """Capabilities advertised by this agent (A2A spec §4)."""

    streaming: bool = True  # message/stream supported
    pushNotifications: bool = False  # webhook push not yet supported
    stateTransitionHistory: bool = False  # Task.history not populated


class AgentCard(BaseModel):
    """A2A agent card describing the agent, its interfaces, and skills."""

    protocolVersions: list[str]

    name: str
    version: str
    description: str

    supportedInterfaces: list[SupportedInterface]

    capabilities: AgentCapabilities

    securitySchemes: dict[str, SecurityScheme]
    security: list[dict[str, list[str]]]

    defaultInputModes: list[str]
    defaultOutputModes: list[str]

    skills: list[Skill]
