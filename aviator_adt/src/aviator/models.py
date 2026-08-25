"""Define reuseable Models."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, Literal

from langgraph.graph.message import AnyMessage, add_messages
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_serializer

from aviator.utils.reducers import reduce_where_clauses

if TYPE_CHECKING:
    from langchain_core.documents import Document

logger = logging.getLogger(__name__)


#
# Flexible WhereClauseReferenceModel
#


class WhereClauseReferenceModel(BaseModel):
    """Flexible where clause supporting AND/OR logic and aliasing for workspace/document IDs."""

    workspace_id: Any = Field(
        default=None,
        alias="workspaceID",
        name="workspace_id",
        description="Unique identifier of the workspace",
    )

    document_id: Any = Field(
        default=None,
        alias="documentID",
        name="document_id",
        description="Unique identifier of the document",
    )

    # Allow arbitrary extra fields (e.g. plugin-specific filter keys like @containerID, docbaseName)
    # populate_by_name=True + alias already handles both workspace_id/workspaceID and document_id/documentID natively
    model_config = ConfigDict(extra="allow", populate_by_name=True, validate_assignment=True)

    @model_serializer(mode="wrap")
    def serialize_without_nones(
        self, handler: Callable[["WhereClauseReferenceModel"], dict[str, object]]
    ) -> dict[str, object]:
        """Exclude None-valued fields (including aliased ones) from serialized output."""
        return {k: v for k, v in handler(self).items() if v is not None}


#
# Reference and Chunk models
#


class Chunk(BaseModel):
    """Lean chunk model used in context payloads and citations."""

    model_config = ConfigDict(populate_by_name=True)

    chunk_id: str = Field(..., alias="CHUNK_ID", description="Unique identifier for this chunk")
    text: str = Field(..., alias="CHUNK_CONTENT", description="The text content of this chunk")
    document_id: str | None = Field(default=None, description="ID of the source document for this chunk")
    workspace_id: str | None = Field(default=None, description="ID of the workspace containing this chunk")
    distance: float | None = Field(default=None, description="Similarity distance/relevance score for this chunk")
    start_index: int | None = Field(
        default=None,
        description="Starting character position of this chunk in the original document",
    )


class ContextDocumentModel(BaseModel):
    """Grouped context model: one document with its associated chunks."""

    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(
        ...,
        alias="DOCUMENT_ID",
        description="ID of the source document",
        exclude_none=True,
        coerce_numbers_to_str=True,
    )
    workspace_id: str | None = Field(
        default=None,
        alias="WORKSPACE_ID",
        description="ID of the workspace containing the document",
        exclude_none=True,
        coerce_numbers_to_str=True,
    )
    metadata: dict | None = Field(default=None, description="Document-level metadata")
    chunks: list[Chunk] = Field(default_factory=list, description="Chunks that belong to this document")

    @classmethod
    def from_langchain_document(cls, doc: "Document", distance: float | None = None) -> "ContextDocumentModel":
        """Create a single-document context model from a LangChain document."""

        document_id = doc.metadata.get("document_id") or doc.metadata.get("documentID")
        workspace_id = doc.metadata.get("workspace_id") or doc.metadata.get("workspaceID")

        chunk = Chunk(
            chunk_id=doc.id,
            text=doc.page_content,
            document_id=document_id,
            workspace_id=workspace_id,
            distance=distance,
            start_index=doc.metadata.get("start_index"),
        )

        return cls(
            document_id=document_id,
            workspace_id=workspace_id,
            metadata=dict(doc.metadata),
            chunks=[chunk],
        )


class ReferenceChunkModel(BaseModel):
    """Define the Model for individual chunk items in Reference."""

    model_config = ConfigDict(populate_by_name=True)

    chunk_id: str = Field(
        ...,
        alias="chunkID",
        description="ID of the chunk used for the citation",
        coerce_numbers_to_str=True,
    )
    citation: int | None = Field(
        default=None,
        description="The citation number assigned to this chunk (can be null)",
    )
    content: Any = Field(..., description="The text content of this chunk (string or structured content)")
    source: str = Field(default="LLM", description="Source type for the chunk (e.g., 'LLM')")
    distance: float | None = Field(default=None, description="Similarity distance/relevance score for this chunk")


class ReferenceModel(BaseModel):
    """Define Model for a single Reference.

    A single reference consists of a document, a workspace the document is in, and
    a list of matching chunks in that documents.

    """

    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(
        ...,
        exclude=True,
        description="ID of the source document",
        coerce_numbers_to_str=True,
    )
    workspace_id: str | None = Field(
        default=None,
        exclude=True,
        description="ID of the workspace containing the document",
        coerce_numbers_to_str=True,
    )
    distance: float | None = Field(
        default=None,
        description="Similarity distance/relevance score for this reference",
    )

    chunks: list[ReferenceChunkModel] = Field(default_factory=list, description="List of chunks with citations")

    @computed_field
    @property
    def metadata(self) -> dict:
        """Computed metadata containing workspace and document IDs with backwards compatibility fields."""
        metadata = {}
        if self.document_id is not None:
            metadata["documentID"] = self.document_id
        if self.workspace_id is not None:
            metadata["workspaceID"] = self.workspace_id
        # Backwards compatibility: include distance at metadata level
        if self.distance is not None:
            metadata["distance"] = self.distance
        # Backwards compatibility: include content and source from chunks
        if self.chunks:
            metadata["content"] = {
                "chunks": [chunk.content for chunk in self.chunks],
                "source": self.chunks[0].source,
            }
        return metadata


class StateModel(BaseModel):
    """Define state for LangGraph state graph."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    where: Annotated[list[WhereClauseReferenceModel], reduce_where_clauses] = Field(
        default_factory=list,  # ensures a fresh list per instance
        description=(
            "Context restrictions (grounding) on workspaces and documents, provided as a list of references",
            "Filter clause for RAG queries. A list of filter objects where each "
            "object is OR'd with others. Within each object, conditions are AND'd. "
            "Supports: simple equality, custom metadata keys, "
            "_NEQ_ (not equal), and $contains operator.",
        ),
    )
    messages: Annotated[list[AnyMessage], add_messages] = Field(
        default_factory=list,
        description="List of messages exchanged in the chat session",
    )
    user: dict | None = Field(default=None, description="User performing the chat request.")
    rewrite_counter: int = Field(default=0, description="Counter for the number of rewrites performed")
    query: str | None = Field(
        default=None,
        description="Initial question that was used to initiate the Graph.",
    )
    refusal_reason: str | None = Field(
        default=None,
        description="Structured refusal reason for the current turn, if the system blocked the request.",
    )
    inlineCitation: bool = Field(
        default=True,
        description="Whether to extract and display inline citations from responses",
    )

    references: list[ReferenceModel] = Field(
        default_factory=list,
        description="List of references grouped by document/workspace",
    )
    caller: str | None = Field(
        default=None,
        description="Caller type for the response",
    )
    extensions: dict = Field(
        default_factory=dict,
        description="Dictionary to hold any extension data for the state, to be used by plugins.",
    )
    summarization_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Context for tracking summarization state to avoid re-summarizing on every call",
    )
    summary_disclaimer: str | None = Field(
        default=None,
        description=(
            "Pre-formatted disclaimer appended to the summary response when ingestion is incomplete. "
            "Set by the generate_summary tool when summaries were found for fewer documents than were "
            "accessible (ingestion may be in progress). None when the tool was not invoked or all "
            "accessible documents have summaries."
        ),
    )

    def get_workspace_ids(self) -> list | None:
        """Extract a list of unique workspace IDs from the context 'where' clause.

        Returns:
            list | None:
                The list of unique workspace IDs in the context or None if not found.

        """

        ids = [
            where.workspace_id
            for where in self.where
            if isinstance(where, WhereClauseReferenceModel) and where.workspace_id is not None
        ]

        # Make sure we have unique IDs:
        return list(set(ids)) or None

    def get_document_ids(self) -> list | None:
        """Extract a list of unique document IDs from the context 'where' clause.

        Returns:
            list | None:
                The list of unique document IDs in the context or None if not found.

        """

        ids = [
            where.document_id
            for where in self.where
            if isinstance(where, WhereClauseReferenceModel) and where.document_id is not None
        ]

        # Make sure we have unique IDs:
        return list(set(ids)) or None


#
# API Models
#


class ChatMessageModel(BaseModel):
    """Define single Chat message."""

    author: Literal["human", "user", "ai", "assistant", "function", "tool", "system", "developer"]
    content: str


class ChatRequestModel(BaseModel):
    """Define chat request payload."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "context": "{}",
                    "inlineCitation": True,
                    "messages": [{"author": "user", "content": "What is your Name?"}],
                    "where": [{"documentID": "12345"}, {"workspaceID": "67890"}],
                    "caller": "",
                }
            ]
        },
    )

    context: str | None = None
    inlineCitation: bool = Field(
        default=True,
        description="Whether to extract and display inline citations from responses",
    )
    messages: list[ChatMessageModel]
    where: list[WhereClauseReferenceModel] = Field(
        default_factory=list,
        description="Context restrictions (grounding) on workspaces and documents, provided as a list of flexible where clauses",
    )
    caller: str | None = Field(
        default=None,
        description="Caller identifier for the request",
    )


class QueryMetadataModel(BaseModel):
    """Metadata about query normalization and processing."""

    originalQuery: str = Field(..., description="The original query as submitted by the user")
    usedQuery: str | list[str] = Field(..., description="The query actually used for search (may differ from original)")


class ChatResponseModel(BaseModel):
    """Define a chat response."""

    result: str | None
    context: str | None
    where: list[WhereClauseReferenceModel] = Field(
        default_factory=list,
        description="Context restrictions (grounding) on workspaces and documents, provided as a list of flexible where clauses",
    )
    references: list[ReferenceModel] = Field(
        default_factory=list,
        description="List of references grouped by document/workspace",
    )
    queryMetadata: QueryMetadataModel | None = Field(
        default=None, description="Metadata about query normalization and processing"
    )


class DirectChatRequestModel(BaseModel):
    """Define direct chat request payload that bypasses the graph."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "messages": [
                        {"author": "user", "content": "What is the capital of France?"},
                    ],
                    "options": {"model": "gemini-2.5-flash-lite", "temperature": 0.7},
                    "chatID": "optional-chat-session-id",
                }
            ]
        },
    )

    messages: list[ChatMessageModel] = Field(..., description="List of messages in the conversation", min_length=1)
    options: dict | None = Field(default=None, description="Options for the LLM model")
    chat_id: str | None = Field(
        default=None,
        alias="chatID",
        description="Unique identifier for the chat session",
    )


class DirectChatResponseModel(BaseModel):
    """Define direct chat response."""

    model_config = ConfigDict(populate_by_name=True)

    result: str = Field(..., description="The response from the LLM")
    chat_id: str | None = Field(default=None, alias="chatID", description="Chat session identifier if provided")


class FeedbackRequestModel(BaseModel):
    """Feedback request model."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "question": "What is your Name?",
                    "answer": "My name is Aviator.",
                    "rating": "UP",
                }
            ]
        },
    )

    question: str
    answer: str
    rating: Literal["UP", "DOWN"]
    context: str | None = Field(default=None, description="Optional context for the feedback")
    trace_id: str | None = Field(default=None, description="Trace ID for the interaction being rated")
    comment: str | None = Field(
        default=None,
        description="Optional comment providing more details about the feedback",
    )


class FormattedResponseModel(BaseModel):
    """Response with formatted answer and references."""

    formatted_answer: str = Field(..., description="The formatted and improved answer")
    references: list[ReferenceModel] = Field(default_factory=list, description="List of references with chunks")


class ContextRequestModel(BaseModel):
    """Define chat request payload."""

    model_config = ConfigDict(populate_by_name=True, validate_by_alias=True)

    query: str | None = None
    threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Similarity threshold for retrieving relevant chunks. Decimal between 0 and 1.",
    )
    num_results: int = Field(default=20, description="Number of results to retrieve", alias="numResults")
    metadata: list[WhereClauseReferenceModel] = Field(
        default_factory=list,  # ensures a fresh list per instance
        description="Context restrictions (grounding) on workspaces and documents, provided as a list of flexible where clauses",
    )


class ContextResposeModel(BaseModel):
    """Define context response payload."""

    documents: list[ContextDocumentModel] = Field(
        default_factory=list,
        description="List of retrieved documents with nested chunks from the vector store",
    )


#
# Embedding Models
#


class EmbeddingRequest(BaseModel):
    """Define request Model for embeddings."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "content": "This is a sample text to be embedded.",
                    "operation": "add",
                    "metadata": {
                        "documentID": "12345",
                        "workspaceID": "67890",
                    },
                }
            ]
        }
    )

    content: str | dict | None = None
    operation: Literal["add", "delete", "update"] = "add"
    metadata: dict = Field(default_factory=dict)


class WorkspaceSummary(BaseModel):
    """Define request Model for workspace or document summary generation."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "content": "This is a sample text to be summarized.",
                    "metadata": {
                        "documentID": "12345",
                        "workspaceID": "67890",
                    },
                }
            ]
        }
    )

    content: str | dict | None = None
    operation: Literal["add", "delete", "update"] = "add"
    metadata: dict = Field(default_factory=dict)


class DocumentSummary(BaseModel):
    """Define response Model for document summary generation."""

    summary: str = Field(..., description="The generated summary of the document")
    title: str = Field(..., description="Title/Headline for the document being summarized")


class DirectEmbedRequest(BaseModel):
    """Define request model for direct embedding generation."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "content": [
                        "This is the first text to embed.",
                        "Here is another text for embedding.",
                    ],
                }
            ]
        }
    )

    content: list[str] = Field(..., description="List of text strings to generate embeddings for")


class DirectEmbedResponse(BaseModel):
    """Define response model for direct embedding generation."""

    vectors: list[list[float]] = Field(..., description="List of embedding vectors, each vector is a list of floats")
    model: str = Field(..., description="Name of the embedding model used")


#
# Structured Output Models
#


class GradeModel(BaseModel):
    """Grade Information using a binary score for relevance check."""

    binary_score: str = Field(description="Relevance score: 'yes' if relevant, or 'no' if not relevant")


#
# Queue Status Model
#


class WorkerAutoscalerModel(BaseModel):
    """Autoscaler runtime details for a worker."""

    current: int | None = Field(default=None, description="Current worker pool size")
    min: int | None = Field(default=None, description="Minimum worker pool size")
    max: int | None = Field(default=None, description="Maximum worker pool size")


class WorkerInfoModel(BaseModel):
    """Runtime details for a single Celery worker."""

    status: str = Field(description="Worker status (online/offline)")
    uptime_seconds: int | None = Field(default=None, description="Worker uptime in seconds")
    autoscaler: WorkerAutoscalerModel | None = Field(
        default=None,
        description="Autoscaler configuration and current size when autoscaling is enabled",
    )
    active_tasks: int | None = Field(default=None, description="Number of tasks currently executing")
    tasks_executed: dict[str, int] | None = Field(
        default=None, description="Lifetime task execution counts by task name"
    )


class QueueStatusModel(BaseModel):
    """Response model for the /v1/queue-status endpoint."""

    broker_type: str = Field(description="Configured broker transport type")
    broker_host: str = Field(description="Broker hostname")
    broker_connection: str = Field(description="Broker connection status")
    queues: dict[str, int] = Field(default_factory=dict, description="Queue names mapped to their message counts")
    workers: dict[str, WorkerInfoModel] = Field(
        default_factory=dict, description="Worker names mapped to their runtime details"
    )
    registered_tasks: list[str] = Field(default_factory=list, description="List of registered Celery task names")
    warning: str | None = Field(default=None, description="Optional warning message")
    error: str | None = Field(default=None, description="Optional error message")


#
# RAG Query Model
#


class RAGQueryModel(BaseModel):
    """Query Model for RAG queries."""

    search_string: str = Field(..., description="Search string to be used to find matching documents")
    search_filter: list[WhereClauseReferenceModel] = Field(
        default=[],
        description="List of items to filter the search. If empty list, all documents and workspaces in scope are searched.",
    )
    limit: int | None = Field(default=None, description="Maximum number of results to return")
    is_summary: bool | None = Field(
        default=None,
        description="Indicates whether the query is for a summary or detailed information",
    )


#
# DevTools Models
#


class DocumentChunksResponseModel(BaseModel):
    """Response model for document chunks lookup."""

    document_id: str = Field(..., description="The document ID that was queried")
    exists: bool = Field(..., description="Whether embeddings exist for this document")
    chunk_count: int = Field(..., description="Number of chunks found for this document")
    summary: bool = Field(..., description="Whether a summary exists for this document")
