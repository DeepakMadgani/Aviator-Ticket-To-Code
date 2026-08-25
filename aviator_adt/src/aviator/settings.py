"""Settings class for Aviator."""

import contextlib
import logging
import math
import os
import tempfile
from enum import StrEnum
from typing import Literal

from pydantic import AmqpDsn, Field, HttpUrl, PostgresDsn, RedisDsn, computed_field, field_validator
from pydantic_settings import BaseSettings

from aviator.settings_secrets import (
    get_anthropic_api_key,
    get_aws_bedrock_access_key,
    get_aws_bedrock_secret_key,
    get_azure_openai_api_key,
    get_broker_password,
    get_mistral_api_key,
    get_openai_api_key,
    get_postgres_password,
)
from aviator.validators import PubSubUrl

logger = logging.getLogger(__name__)


class LLMProvider(StrEnum):
    """Supported LLM providers."""

    GOOGLE_GENAI = "google_genai"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    AWS_BEDROCK = "aws_bedrock"
    ANTHROPIC = "anthropic"
    MISTRAL = "mistral"


class EmbeddingsProvider(StrEnum):
    """Supported embeddings providers."""

    GOOGLE_GENAI = "google_genai"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    AWS_BEDROCK = "aws_bedrock"
    MISTRAL = "mistral"


class Settings(BaseSettings):
    """Application settings."""

    #
    # General Settings
    #
    bind_address: str = Field(default="0.0.0.0", description="Interface to bind")  # noqa: S104
    bind_port: int = Field(default=3000, description="Port to bind")
    workers: int = Field(default=1, description="Number of workers to use for the API background tasks")
    root_path: str = Field(default="", description="Root path for the ToolBox API")
    aviator_chat_service_endpoint: str | None = Field(
        default=None,
        description=(
            "Internal base URL used by A2A handlers for loopback calls to /v1/chat and /v1/chat/stream. "
            "Read from the AVIATOR_CHAT_SERVICE_ENDPOINT environment variable. "
            "Defaults to the cluster-internal service URL for the dev-qa namespace."
        ),
    )
    openapi_url: str = Field(default="/api/openapi.json", description="OpenAPI URL")
    reload: bool = Field(default=False, description="Enable or disable the autoreload feature")
    content_system: str | None = Field(default=None, description="Content System for authentication and authorization")
    content_system_url: HttpUrl | None = Field(
        default=None,
        description="Base URL for the Content System, including scheme and path. E.g., https://otcs.domain.tld/cs/cs",
    )
    otds_url: HttpUrl | None = Field(
        default=HttpUrl("http://otds"),
        description="Base URL for the OTDS base URL, including scheme and path. E.g., https://otds.domain.tld/",
    )
    add_doc_metadata_to_context: bool = Field(
        default=False, description="Enable fetching and adding document-level metadata to context"
    )
    enable_bias_detection: bool = Field(
        default=True,
        description="Enable or disable the ethical bias and legal compliance filter for queries",
    )

    @computed_field
    @property
    def otds_jwks_url(self) -> HttpUrl | None:
        """Computed field to derive the OTDS JWKS URL."""
        if self.otds_url:
            return HttpUrl(f"{self.otds_url}otdsws/oauth2/jwks")
        return None

    @computed_field
    @property
    def azure_openai_computed_endpoint(self) -> str | None:
        """Computed field to derive Azure OpenAI endpoint from instance name if not explicitly set."""
        if self.azure_openai_endpoint:
            # If explicit endpoint is provided, use it
            return self.azure_openai_endpoint
        if self.azure_openai_instance_name:
            # Construct endpoint from instance name
            return f"https://{self.azure_openai_instance_name}.openai.azure.com/"
        return None

    #
    # Dev Tools
    #
    dev_tools: bool = Field(default=False, description="Enable or disable the development tools")

    #
    # Plugin Settings
    #
    plugin_path: str | None = Field(
        default="/plugins",
        description="Directory path where plugin packages (.tar.gz) are located for auto-installation at startup",
    )
    uv_cache_dir: str = Field(
        default=f"{tempfile.gettempdir()}/uv_cache",
        description="Cache directory for UV package manager when installing plugins",
    )

    #
    # Log Settings
    #
    loglevel: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    logfolder: str = Field(
        default=f"{tempfile.gettempdir()}/aviator",
        description="Logfolder for the Aviator ToolBox.",
    )

    #
    # Message Broker Configuration
    #
    broker_url: RedisDsn | AmqpDsn | PubSubUrl | None = Field(
        default=None, description="URL to access the broker. Set this, or the broker_* settings."
    )
    broker_type: Literal["amqp", "redis", "pubsub"] | None = Field(default=None, description="Type of Message Broker")
    broker_user: str | None = Field(default=None, description="Username to access the message broker")
    broker_password: str | None = Field(default=None, description="Password to access the message broker")
    broker_host: str | None = Field(default=None, description="Host to access the message broker")
    broker_vhost: str | None = Field(default=None, description="vHost used in the message broker")
    broker_queue_name: str = Field(default="aviator-embeddings", description="Name of the Broker Queue")
    migration_enabled: bool = Field(
        default=False,
        description="Enable CSAI migration task registration and migration queue subscription on the worker.",
    )
    broker_consistent_hash_enabled: bool = Field(
        default=False,
        description="Enable RabbitMQ consistent hash exchange for document-level routing. "
        "Ensures all tasks for the same document are processed by the same worker, preventing race conditions.",
    )
    broker_worker_queue_count: int = Field(
        default=4,
        description="Number of worker queues to create when using consistent hash exchange. "
        "Should match or exceed the number of worker replicas.",
    )
    broker_summary_queue_name: str = Field(
        default="aviator-summaries", description="Name of the Broker Queue used for summary generation tasks"
    )
    celery_worker_role: Literal["default", "summary"] = Field(
        default="default",
        description="Role of the Celery worker, determining which queues it consumes from. "
        "Set to 'summary' for workers dedicated to summary generation tasks, which will only consume from the summary queue.",
    )

    # Google Pub/Sub specific settings
    pubsub_project_id: str | None = Field(default=None, description="Google Cloud project ID for Pub/Sub")
    pubsub_emulator_host: str | None = Field(default=None, description="Pub/Sub emulator host for local development")
    pubsub_acknowledge_deadline_seconds: int = Field(
        default=240, description="Pub/Sub message acknowledgment deadline in seconds"
    )
    pubsub_subscription_name_prefix: str | None = Field(default="aviator-", description="Pub/Sub subscription prefix")

    #
    # Vector Store Config
    #
    default_schema: str = Field(
        default="public",
        description="Default PostgreSQL schema for unscoped vector store data.",
    )
    multi_tenant_enabled: bool = Field(
        default=True,
        description="Enable schema-per-tenant isolation. When False, all data is stored in the default schema.",
    )
    tenant_api_enabled: bool = Field(
        default=False,
        description=(
            "Enable the /v1/tenants CRUD REST API for managing tenant schemas. "
            "Requires multi_tenant_enabled!=False to be meaningful."
        ),
    )
    tenant_schema_prefix: str = Field(
        default="tenant_",
        description="Prefix for tenant PostgreSQL schema names (e.g. 'tenant_acme').",
    )
    tenant_id_pattern: str = Field(
        default=r"^[a-zA-Z0-9_-]{2,63}$",
        description="Regex pattern to validate tenant identifiers (includes allowed characters and length constraints).",
    )
    vector_store: Literal["memory", "pgvectorstore"] = Field(
        "pgvectorstore",
        description="Type of vector store to use. Allowed value: 'pgvector' (currently the only supported option).",
    )
    vector_store_table_name: str = Field(default="aviator", description="Table name for the vector store.")
    summary_table_name: str = Field(
        default="workspace_document_summaries", description="Table name for document summaries."
    )
    vector_store_metadata_column: str = Field(
        default="langchain_metadata",
        description="Name of the JSONB metadata column used by langchain-postgres PGVectorStore.",
    )
    vector_size: int | None = Field(default=None, description="Size of the vectors to store.")
    vector_score_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Score threshold for vector similarity searches (decimal between 0 and 1).",
    )
    vector_storage_type: Literal["vector", "halfvec"] = Field(
        default="vector",
        description="Storage mode for embeddings: 'vector' (float32, full precision) or 'halfvec' (float16, 50%% smaller, slight recall loss).",
    )
    vector_index_type: Literal["hnsw", "ivfflat"] | None = Field(
        default=None,
        description="ANN index type for embeddings: 'hnsw', 'ivfflat', or None (sequential scan, no index). When using 'ivfflat', Aviator derives recommended lists/probes values unless explicit overrides are provided.",
    )
    hnsw_m: int = Field(
        default=24,
        ge=5,
        le=64,
        description="HNSW index parameter: max number of bidirectional links per node. Higher = better recall but larger index.",
    )
    hnsw_ef_construction: int = Field(
        default=200,
        ge=10,
        le=500,
        description="HNSW index parameter: search depth during index construction. Higher = better index quality but slower builds.",
    )
    hnsw_ef_search: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="HNSW index parameter 'ef_search': size of dynamic candidate list for search. Higher values improve recall but slow down queries. Set per connection after establishment.",
    )
    ivfflat_lists: int | None = Field(
        default=4000,
        ge=1,
        description="Advanced IVFFlat override for the number of inverted lists. If unset, Aviator derives it as floor(sqrt(total_chunks)) using the current embedding-row count.",
    )
    ivfflat_probes: int | None = Field(
        default=63,
        ge=1,
        description="Advanced IVFFlat override for the number of lists to probe during search. If unset, Aviator derives it as floor(sqrt(lists)).",
    )
    maintenance_work_mem: str = Field(
        default="512MB",
        description="PostgreSQL maintenance_work_mem for index creation. Automatically escalated if insufficient.",
    )
    postgres_connection: PostgresDsn | None = Field(
        default=None,
        description="Connection string for the database. Either use this, or the individual postgres_* settings.",
    )

    @field_validator("broker_url", "postgres_connection", mode="before")
    @classmethod
    def validate_connection_strings(cls, v: str | None) -> str | None:
        """Convert empty connection string values to None."""
        if v == "" or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    postgres_connection_settings: dict = Field(
        default={
            "pool_size": 10,
            "max_overflow": 20,
            "pool_timeout": 10,
            "pool_recycle": 1800,
            "pool_pre_ping": True,
            "pool_use_lifo": True,
            "connect_args": {
                "application_name": "Content Aviator RAG",
                "options": "-c statement_timeout=300000 -c idle_in_transaction_session_timeout=600000",
            },
        },
        description="Additional connection settings for PostgreSQL (e.g., SSL mode, connection timeout). Note: hnsw.ef_search or ivfflat.probes will be dynamically added based on vector_index_type setting.",
    )

    database_backend: Literal["postgres"] = Field(
        default="postgres",
        description="Backend for the relational database adapter.",
    )

    text_splitter_chunk_size: int = Field(default=1000, description="Desired chunk size for stored embeddings.")
    text_splitter_chunk_overlap: int = Field(default=50, description="Overlap op chunk when ther are splitted.")

    # Table splitter settings - separate configuration for table content
    table_splitter_chunk_size: int = Field(default=2000, description="Desired chunk size for table embeddings.")
    table_splitter_chunk_overlap: int = Field(default=100, description="Overlap of chunks when tables are splitted.")

    # Embedding batch size settings
    embedding_batch_size: int = Field(
        default=50, gt=0, description="Maximum number of document chunks to embed per API call."
    )
    embedding_batch_max_chars: int = Field(
        default=30000,
        gt=0,
        description="Maximum total characters across all chunks in a single embedding batch. Batches are flushed when this character limit is reached, even if embedding_batch_size has not been hit.",
    )
    embedding_batch_max_wait_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Maximum seconds to wait for more chunks before flushing a partial embedding batch. "
        "Acts as a safety net to flush remaining chunks when the queue goes idle. "
        "The buffer primarily flushes based on character and count limits, not this timer.",
    )

    postgres_user: str | None = Field(default=None, description="User to connect to the postgres database")
    postgres_password: str | None = Field(default=None, description="Password to connect to the postgres database")
    postgres_database: str | None = Field(default=None, description="Database to connect to the postgres database")
    postgres_port: int | None = Field(default=None, description="Database port to connect to the postgres database")
    postgres_host: str | None = Field(default=None, description="Hostname to connect to the postgres database")
    postgres_pool_size: int = Field(
        default=10, description="Maximum number of connections in the PostgreSQL connection pool"
    )

    #
    # Checkpointer Settings
    #

    checkpointer: Literal["postgres", "memory"] = Field(default="postgres", description="Type of checkpointer to use.")

    #
    # LLM Settings
    #
    llm_provider: LLMProvider = Field(default=LLMProvider.GOOGLE_GENAI)
    llm_model: str | None = None
    llm_model_assistant: str | None = None
    llm_location: str = Field(default="europe-west4")
    llm_base_url: str | None = Field(
        default=None, description="Base URL for LLM API (used for custom OpenAI-compatible endpoints)"
    )

    #
    # Embeddings Settings
    #
    embeddings_provider: EmbeddingsProvider | None = Field(
        default=None, description="Embeddings provider. If None, uses the same provider as llm_provider."
    )
    embeddings_model: str | None = Field(default=None)
    embeddings_task_type: str | None = Field(
        default="DEFAULT",
        description="Task type for Google GenAI embeddings when embedding documents (e.g. RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY, SEMANTIC_SIMILARITY, CLASSIFICATION, CLUSTERING, DEFAULT)",
    )

    embeddings_base_url: str | None = Field(
        default=None, description="Base URL for embeddings API (used for custom OpenAI-compatible endpoints)"
    )

    #
    # Provider API Keys
    #
    openai_api_key: str | None = Field(default=None)

    # Azure OpenAI specific settings
    azure_openai_endpoint: str | None = Field(
        default=None,
        description="Azure OpenAI endpoint URL (e.g., https://your-resource.openai.azure.com/). If not provided, will be constructed from instance_name.",
    )
    azure_openai_instance_name: str | None = Field(
        default=None,
        description="Azure OpenAI instance/resource name (used to construct endpoint if not explicitly provided)",
    )
    azure_openai_api_key: str | None = Field(default=None, description="Azure OpenAI API key")
    azure_openai_api_version: str = Field(default="2024-10-21", description="Azure OpenAI API version")
    azure_openai_deployment_name: str | None = Field(
        default=None, description="Azure OpenAI deployment name for the LLM model"
    )
    azure_openai_deployment_name_assistant: str | None = Field(
        default=None, description="Azure OpenAI deployment name for the assistant LLM model"
    )
    azure_openai_embeddings_deployment_name: str | None = Field(
        default=None, description="Azure OpenAI deployment name for embeddings model"
    )

    # Anthropic specific settings
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key for Claude models.")

    # Mistral specific settings
    mistral_api_key: str | None = Field(default=None, description="Mistral AI API key.")

    # AWS specific settings
    aws_bedrock_access_key: str | None = Field(
        default=None, description="AWS access key ID for Bedrock API authentication."
    )
    aws_bedrock_secret_key: str | None = Field(
        default=None, description="AWS secret access key for Bedrock API authentication."
    )
    aws_bedrock_region: str | None = Field(
        default="us-west-2",
        description="AWS region where Bedrock service is available (e.g., 'us-east-1', 'us-west-2')",
    )

    llm_safety_settings: str = Field(
        default='[{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}, {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}, {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"}]',
        description='JSON array of safety settings for Google Gemini. Only applied to the GOOGLE_GENAI provider. Each entry must be an object with \'category\' and \'threshold\' string keys. Accepted category values are Google HarmCategory enum names (for example: HARM_CATEGORY_HARASSMENT, HARM_CATEGORY_HATE_SPEECH, HARM_CATEGORY_DANGEROUS_CONTENT, HARM_CATEGORY_SEXUALLY_EXPLICIT). Accepted threshold values are Google HarmBlockThreshold enum names (for example: BLOCK_NONE, BLOCK_ONLY_HIGH, BLOCK_MEDIUM_AND_ABOVE, BLOCK_LOW_AND_ABOVE). Example: [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}].',
    )

    llm_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Sampling temperature for the LLM (decimal between 0 and 1).",
    )
    top_k: int | None = Field(default=40)
    top_p: float | None = Field(default=0.8)
    max_tokens: int = Field(default=65536, description="Maximum tokens for LLM responses.")
    total_token_size: int = Field(default=20000, description="Total LLM context window size in tokens")
    rag_default_limit: int = Field(default=20, description="Default limit for RAG searches when not specified.")

    @computed_field(description="Chunk limit for list-type RAG queries: 70% of total_token_size")
    @property
    def rag_list_query_limit(self) -> int:
        """Return the max number of chunks that fit in 70% of the LLM token budget."""
        avg_tokens_per_chunk = max(1, self.text_splitter_chunk_size // 4)
        return math.ceil(self.total_token_size * 0.70 / avg_tokens_per_chunk)

    summary_batch_token_limit: int = Field(
        default=70000,
        description="When accumulated summary tokens reach this threshold, flush and generate a higher-level summary.",
    )
    summary_model_input_token_limit_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=0.9,
        description="Threshold (0.0 to 1.0) of the model's input token limit to use for batching document content during summarization.",
    )
    summary_tool_max_token: int | None = Field(
        default=300, description="Maximum output tokens for the summary generation tool."
    )
    #
    # Message History Management Settings
    #
    message_history_enabled: bool = Field(
        default=True,
        description="Enable message history management (summarization or trimming)",
    )
    message_history_strategy: Literal["summarization", "trimming"] = Field(
        default="summarization",
        description="Strategy for managing message history: 'summarization' or 'trimming'",
    )
    message_history_max_tokens: int = Field(
        default=5120,
        description="Maximum tokens for message history before triggering summarization/trimming",
    )
    message_history_max_summary_tokens: int = Field(
        default=2048,
        description="Maximum tokens for the summary when using summarization strategy",
    )

    document_summary_max_tokens: int = Field(
        default=1000,
        description="Maximum number of tokens for a document",
    )

    summary_llm_request_timeout: int | None = Field(
        default=None,
        description="Timeout in seconds specifically for summary generation LLM calls. Useful for large documents that take longer to process.",
    )

    enable_debug_tracing: bool = Field(
        default=False,
        description="Enable detailed debug tracing via OpenTelemetry, including auto-instrumentation for common libraries (FastAPI, Requests, Psycopg, GoogleGenAiSdk).",
    )

    #
    # Google Cloud Settings
    #
    google_cloud_project: str | None = Field(
        default=None,
        env="GOOGLE_CLOUD_PROJECT",
        description="Google Cloud project ID. If not set, will try to auto-detect from GOOGLE_CLOUD_PROJECT environment variable or service account credentials.",
    )

    #
    # Secrets Management Settings
    #
    secrets_manager: Literal["environment", "google", "vault", "kubernetes"] = Field(
        default="environment",
        description="Secrets management provider. 'environment' uses env vars, 'google' uses Google Cloud Secret Manager, 'vault' uses HashiCorp Vault, 'kubernetes' uses Kubernetes secrets.",
    )
    vault_url: str | None = Field(
        default=None,
        description="HashiCorp Vault server URL when secrets_manager='vault'.",
    )
    vault_config: str | None = Field(
        default=None,
        description="JSON configuration string for HashiCorp Vault authentication when secrets_manager='vault'.",
    )
    kubernetes_config: str | None = Field(
        default=None,
        description="JSON configuration string for Kubernetes API access when secrets_manager='kubernetes'. Contains endpoint, namespace override, etc.",
    )
    secrets_pgvector_password_key: str | None = Field(
        default=None,
        description="Secret name/key for PostgreSQL password when using external secret managers.",
    )
    secrets_broker_password_key: str | None = Field(
        default=None,
        description="Secret name/key for broker password when using external secret managers.",
    )
    secrets_openai_api_key: str | None = Field(
        default=None,
        description="Secret name/key for OPENAI_API_KEY when using external secret managers.",
    )
    secrets_azure_openai_api_key: str | None = Field(
        default=None,
        description="Secret name/key for AZURE_OPENAI_API_KEY when using external secret managers.",
    )
    secrets_anthropic_api_key: str | None = Field(
        default=None,
        description="Secret name/key for ANTHROPIC_API_KEY when using external secret managers.",
    )
    secrets_mistral_api_key: str | None = Field(
        default=None,
        description="Secret name/key for MISTRAL_API_KEY when using external secret managers.",
    )
    secrets_aws_bedrock_access_key: str | None = Field(
        default=None,
        description="Secret name/key for AWS_BEDROCK_ACCESS_KEY when using external secret managers.",
    )
    secrets_aws_bedrock_secret_key: str | None = Field(
        default=None,
        description="Secret name/key for AWS_BEDROCK_SECRET_KEY when using external secret managers.",
    )
    # Usage Tracking Settings
    #
    usage_tracking_enabled: bool = Field(
        default=True,
        description="Enable usage tracking and the /v1/usage-stats endpoint. Set to false to disable all tracking.",
    )
    usage_tracking_max_query_days: int = Field(
        default=365,
        description="Maximum number of days allowed in a single stats API query range.",
    )
    usage_tracking_retention_days: int = Field(
        default=365,
        description="Number of days to retain detail transaction rows before cleanup.",
    )
    usage_tracking_tally_retention_days: int = Field(
        default=365,
        description="Number of days to retain daily tally rows before cleanup. Set to 0 to keep tallies indefinitely.",
    )
    # 720 h (30 days) is a safe default; 2160 h (90 days) is the recommended upper bound for ideal retention.
    checkpointer_retention_hours: int = Field(
        default=720,
        ge=1,
        le=2160,
        description="Number of hours to retain LangGraph checkpoint rows (checkpoints, blobs, writes) before cleanup. Must be between 1 and 2160 (90 days).",
    )
    usage_tracking_pool_size: int = Field(
        default=5,
        description="Connection pool size for the usage tracking async database pool.",
    )

    #
    # MCP (Model Context Protocol) Settings
    #
    mcp_enabled: bool = Field(
        default=True,
        description="Enable MCP (Model Context Protocol) client functionality",
    )
    mcp_servers: dict[str, dict] = Field(
        default_factory=dict,
        description="Base MCP server configurations. Format: {server_name: {url: str, transport: str, ...}}",
    )
    mcp_connection_timeout: int = Field(
        default=30,
        description="Default connection timeout for MCP servers in seconds",
    )
    mcp_max_retries: int = Field(
        default=3,
        description="Default maximum connection retries for MCP servers",
    )
    mcp_health_check_interval: int = Field(
        default=60,
        description="Default health check interval for MCP servers in seconds",
    )
    mcp_tools_config_key: str = Field(
        default="ALLOWED_MCP_TOOLS",
        description="The tenant_lookup_config key used to store allowed MCP tool names (comma-separated) per subscription.",
    )

    def _get_vector_index_options(self) -> str:
        """Return connection options for vector index runtime parameters."""
        if self.vector_index_type == "hnsw":
            return f"-c hnsw.ef_search={self.hnsw_ef_search}"
        if self.vector_index_type == "ivfflat":
            return f"-c ivfflat.probes={self.ivfflat_probes}"
        return ""

    def _update_postgres_connection_settings(self) -> None:
        """Merge vector index options into postgres_connection_settings."""
        vector_options = self._get_vector_index_options()
        if not vector_options:
            return

        connect_args = self.postgres_connection_settings.setdefault("connect_args", {})
        existing_options = connect_args.get("options", "")

        if existing_options:
            connect_args["options"] = f"{existing_options} {vector_options}"
        else:
            connect_args["options"] = vector_options

    def _get_model_context_limit(self) -> int:
        """Get the context window limit for the configured model.

        Defaults to a safe 128k for unknown/large models if not specified.
        """
        match self.llm_provider:
            case LLMProvider.GOOGLE_GENAI:
                # Gemini 1.5/2.x models typically handle 1M+, but we use 1M as a stable base for lite
                if "flash-lite" in (self.llm_model or ""):
                    return 1000000
                return 1000000
            case LLMProvider.OPENAI:
                if "gpt-4o" in (self.llm_model or ""):
                    return 128000
                elif "gpt-5" in (self.llm_model or ""):
                    return 400000
                return 128000
            case LLMProvider.AWS_BEDROCK:
                if "nova-lite" in (self.llm_model or ""):
                    return 300000
                return 300000
            case LLMProvider.ANTHROPIC:
                return 200000
            case LLMProvider.MISTRAL:
                return 128000
            case _:
                return 128000

    @property
    def summary_content_threshold_tokens(self) -> int:
        """Derived threshold for document content batching based on model limit."""
        return int(self._get_model_context_limit() * self.summary_model_input_token_limit_threshold)

    def model_post_init(self, __context) -> None:  # noqa: PYI063, ANN001
        """Set default values based on llm_provider."""
        # dbconnection
        if not self.postgres_connection:
            user = self.postgres_user or "postgres"

            # Retrieve postgres password using secrets manager if configured.
            try:
                password = get_postgres_password(self)
            except Exception:
                password = ""

            hostname = self.postgres_host or "pgvector_db"
            port = self.postgres_port or "5432"
            database = self.postgres_database or "postgres"

            self.postgres_connection = PostgresDsn(f"postgresql://{user}:{password}@{hostname}:{port}/{database}")

        self._update_postgres_connection_settings()

        # Retrieve broker password from secrets manager when configured.
        with contextlib.suppress(Exception):
            self.broker_password = get_broker_password(self)

        # broker connection:
        if not self.broker_url:
            btype = self.broker_type or "amqp"
            user = self.broker_user or "admin"
            password = self.broker_password or "admin_pass"
            host = self.broker_host or "rabbitmq"
            vhost = self.broker_vhost or "/"

            if btype == "amqp":
                self.broker_url = AmqpDsn(f"amqp://{user}:{password}@{host}{vhost}")
            elif btype == "redis":
                # !not tested
                self.broker_url = RedisDsn(f"redis://{user}:{password}@{host}")
            elif btype == "pubsub":
                # Google Pub/Sub configuration - use gcpubsub:// URL format
                project_id = self.pubsub_project_id or "aviator-project"
                self.broker_url = PubSubUrl(f"gcpubsub://projects/{project_id}")

        # Set the broker type based on the broker URL
        if isinstance(self.broker_url, PubSubUrl):
            self.broker_type = "pubsub"
        elif isinstance(self.broker_url, AmqpDsn):
            self.broker_type = "amqp"
        elif isinstance(self.broker_url, RedisDsn):
            self.broker_type = "redis"

        match self.llm_provider:
            case LLMProvider.GOOGLE_GENAI:
                self.llm_model = self.llm_model or "gemini-2.5-flash-lite"
                self.llm_model_assistant = self.llm_model_assistant or "gemini-2.5-flash"

                self.embeddings_model = self.embeddings_model or "text-multilingual-embedding-002"
                self.vector_size = self.vector_size or 768

                # Support both JSON file (GOOGLE_APPLICATION_CREDENTIALS) and Workload Identity (GKE)
                has_json_creds = bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
                # GCP sets GOOGLE_CLOUD_PROJECT when Workload Identity is available
                has_workload_identity = bool(os.getenv("GOOGLE_CLOUD_PROJECT"))

                if not has_json_creds and not has_workload_identity:
                    logger.warning(
                        "LLM_Provider is gcp but GOOGLE_APPLICATION_CREDENTIALS is not set. "
                        "Assuming Workload Identity or Google Application Default Credentials will be used."
                    )

            case LLMProvider.OPENAI:
                self.llm_model = self.llm_model or "gpt-5-nano"
                self.llm_model_assistant = self.llm_model_assistant or self.llm_model

                with contextlib.suppress(Exception):
                    self.openai_api_key = get_openai_api_key(self)

                # Set base URLs from environment if provided (for custom OpenAI-compatible endpoints)
                self.llm_base_url = self.llm_base_url or os.getenv("LLM_BASE_URL")
                self.embeddings_base_url = self.embeddings_base_url or os.getenv("EMBEDDINGS_BASE_URL")

                if not self.openai_api_key:
                    logger.critical("LLM_Provider is openai but environment variable OPENAI_API_KEY is not configured")
            case LLMProvider.AZURE_OPENAI:
                # Default deployment names if not provided (users typically set these in environment)
                self.azure_openai_deployment_name = self.azure_openai_deployment_name or "gpt-5-nano"
                self.azure_openai_deployment_name_assistant = (
                    self.azure_openai_deployment_name_assistant or self.azure_openai_deployment_name
                )
                self.azure_openai_embeddings_deployment_name = (
                    self.azure_openai_embeddings_deployment_name or "text-embedding-ada-002"
                )

                with contextlib.suppress(Exception):
                    self.azure_openai_api_key = get_azure_openai_api_key(self)

                # Set vector size for default Azure OpenAI embeddings
                self.vector_size = self.vector_size or 1536

                # Check that either endpoint or instance name is provided
                if not self.azure_openai_computed_endpoint:
                    logger.critical(
                        "Either AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_INSTANCE_NAME is required when llm_provider is azure_openai"
                    )
                if not self.azure_openai_api_key:
                    logger.critical("AZURE_OPENAI_API_KEY is required when llm_provider is azure_openai")
            case LLMProvider.AWS_BEDROCK:
                self.llm_model = self.llm_model or "us.amazon.nova-lite-v1:0"
                self.llm_model_assistant = self.llm_model_assistant or self.llm_model

                with contextlib.suppress(Exception):
                    self.aws_bedrock_access_key = get_aws_bedrock_access_key(self)
                with contextlib.suppress(Exception):
                    self.aws_bedrock_secret_key = get_aws_bedrock_secret_key(self)

                if not (self.aws_bedrock_access_key and self.aws_bedrock_secret_key and self.aws_bedrock_region):
                    logger.error(
                        "AWS_BEDROCK_ACCESS_KEY, AWS_BEDROCK_SECRET_KEY and AWS_BEDROCK_REGION are required parameters as llm_provider is AWS_BEDROCK"
                    )
                    msg = "AWS Bedrock credentials are incomplete."
                    raise ValueError(msg)

            case LLMProvider.ANTHROPIC:
                self.llm_model = self.llm_model or "claude-haiku-4-5-20251001"
                self.llm_model_assistant = self.llm_model_assistant or "claude-sonnet-4-6"

                with contextlib.suppress(Exception):
                    self.anthropic_api_key = get_anthropic_api_key(self)

                if not self.anthropic_api_key:
                    logger.critical("LLM_Provider is anthropic but ANTHROPIC_API_KEY is not configured")

            case LLMProvider.MISTRAL:
                self.llm_model = self.llm_model or "mistral-small-latest"
                self.llm_model_assistant = self.llm_model_assistant or "mistral-large-latest"

                self.embeddings_model = self.embeddings_model or "mistral-embed"
                self.vector_size = self.vector_size or 1024

                with contextlib.suppress(Exception):
                    self.mistral_api_key = get_mistral_api_key(self)

                if not self.mistral_api_key:
                    logger.critical("LLM_Provider is mistral but MISTRAL_API_KEY is not configured")

    @property
    def pubsub_broker_transport_options(self) -> dict:
        """Return Pub/Sub broker transport options for Celery.

        These options must be consistent across all Celery clients (workers and
        producers) to ensure messages are published to and consumed from the
        same topics/subscriptions.
        """
        return {
            "ack_deadline_seconds": self.pubsub_acknowledge_deadline_seconds,
            "polling_interval": 0.1,
            "enable_message_ordering": True,
            "max_outstanding_messages": 1000,
            "max_outstanding_bytes": 1024 * 1024 * 100,  # 100MB
            "message_retention_duration": 7 * 24 * 60 * 60,  # 7 days in seconds
            "queue_name_prefix": self.pubsub_subscription_name_prefix,
        }


settings = Settings()
