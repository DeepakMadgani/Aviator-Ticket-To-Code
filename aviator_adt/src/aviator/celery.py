"""Define message Broker implementation."""

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import sqlalchemy.exc
from celery import Celery, Task, bootsteps
from celery.signals import setup_logging, worker_ready, worker_shutdown
from langchain_core.documents import Document
from langchain_text_splitters import HTMLSectionSplitter, RecursiveCharacterTextSplitter
from langsmith import uuid7
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.threading import ThreadingInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from psycopg_pool import ConnectionPool

import aviator.logfilter  # noqa: F401
from aviator.database.summary_operations import (
    delete_workspace_document_summary,
    upsert_workspace_document_with_summary,
)
from aviator.exceptions import (
    EmbeddingError,
    EmbeddingRetryableError,
    WorkspaceSummaryError,
    WorkspaceSummaryRetryableError,
)
from aviator.models import EmbeddingRequest, WorkspaceSummary
from aviator.plugins import load_embedding_extension
from aviator.services.embedding_buffer import embedding_buffer
from aviator.services.embeddings import EmbeddingsRegistry
from aviator.services.summary import generate_summary
from aviator.services.tenant import tenant_id_to_schema_name, tenant_service
from aviator.services.usage_tracking.recorder import record_transaction_sync
from aviator.settings import settings
from aviator.vector_store import vector_store

# Fix for Windows: psycopg requires SelectorEventLoop
# Must be set BEFORE any async operations
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)


@setup_logging.connect
def _setup_worker_logging(**kwargs: object) -> None:  # noqa: ARG001
    """Redirect Celery worker logging to stdout.

    By default Celery writes all log output to **stderr**.  GCP Cloud
    Logging (and similar log aggregators) treat stderr as ERROR severity,
    which causes every INFO/WARNING message emitted by the worker to
    appear as an error.

    Connecting to the ``setup_logging`` signal prevents Celery from
    configuring logging itself and lets us set up handlers that write to
    **stdout** — matching the behaviour of the API server (uvicorn).
    """
    root = logging.getLogger()

    # Clear any pre-existing handlers (e.g. from basicConfig)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] [%(processName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(settings.loglevel)


def _count_chunks_for_filter(schema_name: str, filter_dict: dict) -> int:
    """Count the number of chunks matching the given metadata filter.

    Queries the vector store table directly via psycopg to get an accurate
    count before a delete operation.
    """
    try:
        import psycopg
        from psycopg import sql as psycopg_sql

        table = settings.vector_store_table_name
        conn_str = settings.postgres_connection.encoded_string()

        conditions = []
        params: list = []
        for key, value in filter_dict.items():
            conditions.append(psycopg_sql.SQL("{} = %s").format(psycopg_sql.Identifier(key)))
            params.append(value)

        where_clause = psycopg_sql.SQL(" AND ").join(conditions)
        query = psycopg_sql.SQL("SELECT COUNT(*) FROM {}.{} WHERE {}").format(
            psycopg_sql.Identifier(schema_name),
            psycopg_sql.Identifier(table),
            where_clause,
        )

        with psycopg.connect(conn_str) as conn, conn.cursor() as cur:
            cur.execute(query, params)
            result = cur.fetchone()
            return result[0] if result else 0
    except Exception:
        logger.exception("Failed to count chunks for filter %s in schema %s", filter_dict, schema_name)
        return 0


HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "worker_heartbeat"
READINESS_FILE = Path(tempfile.gettempdir()) / "worker_ready"
TABLE_SPLITTER = "<TABLE>"
TABLE_TAG_REGEX = re.compile(r"<table(?:\s[^>]*)?>(?:(?!<table\b).)*?</table>", re.IGNORECASE | re.DOTALL)

_pg_pool: ConnectionPool | None = None


def _get_pg_pool() -> ConnectionPool:
    """Return a sync connection pool."""
    global _pg_pool  # noqa: PLW0603
    if _pg_pool is None:
        _pg_pool = ConnectionPool(
            settings.postgres_connection.encoded_string(),
            open=True,
            min_size=1,
            max_size=settings.postgres_connection_settings.get("pool_size", 10),
            kwargs={
                "connect_timeout": 5,
                "prepare_threshold": None,
            },
        )
    return _pg_pool


def _is_database_ready() -> bool:
    """Check if PostgreSQL is reachable for worker readiness."""

    try:
        with _get_pg_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() == (1,)
    except Exception:
        logger.error("Database readiness check failed")
        return False


class BaseTaskWithRetry(Task):
    """Define a base Celery task with automatic retry on failure."""

    autoretry_for = (EmbeddingRetryableError,)
    retry_kwargs = {"max_retries": 7}
    retry_backoff = True


class SummaryTaskWithRetry(Task):
    """Define a Celery task base with automatic retry for transient summary failures."""

    autoretry_for = (WorkspaceSummaryRetryableError,)
    retry_kwargs = {"max_retries": 7}
    retry_backoff = True


class LivenessProbe(bootsteps.StartStopStep):
    """Define a Celery bootstep to create a heartbeat file for liveness probes."""

    requires = {"celery.worker.components:Timer"}

    def __init__(self, worker, **kwargs) -> None:  # noqa: ANN001, ANN003, ARG002
        """Initialize LivenessProbe step."""

        self.requests = []
        self.tref = None

    def start(self, worker) -> None:  # noqa: ANN001
        """Start the liveness probe by scheduling heartbeat updates."""

        self.tref = worker.timer.call_repeatedly(
            1.0,
            self.update_heartbeat_file,
            (worker,),
            priority=10,
        )

    def stop(self, worker) -> None:  # noqa: ANN001, ARG002
        """Remove the heartbeat file on shutdown."""

        HEARTBEAT_FILE.unlink(missing_ok=True)

    def update_heartbeat_file(self, worker) -> None:  # noqa: ANN001, ARG002
        """Update the heartbeat file to signal liveness."""

        HEARTBEAT_FILE.touch()


@worker_ready.connect
def worker_ready(**_) -> None:  # noqa: ANN003
    """Create readiness file when worker is ready."""

    if os.getenv("SENTRY_DSN"):
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.langchain import LangchainIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        logger.info("Initializing Sentry SDK")
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            integrations=[CeleryIntegration(), LangchainIntegration(), SqlalchemyIntegration()],
        )

    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        # Set up OpenTelemetry tracing for the worker
        resource = Resource.create({"service.name": "aviator-worker"})
        provider = TracerProvider(resource=resource)
        processor = BatchSpanProcessor(OTLPSpanExporter())
        provider.add_span_processor(processor)

        trace.set_tracer_provider(provider)

        ThreadingInstrumentor().instrument()
        CeleryInstrumentor().instrument()
        RequestsInstrumentor().instrument()
        PsycopgInstrumentor().instrument()
        SQLAlchemyInstrumentor().instrument()
        LangchainInstrumentor().instrument()

    while True:
        if _is_database_ready():
            logger.info("Worker is ready: database connection successful")
            break
        logger.warning("Worker not ready: database connection failed, retrying in 5 seconds...")
        time.sleep(5)

    READINESS_FILE.touch()


@worker_shutdown.connect
def worker_shutdown(**_) -> None:  # noqa: ANN003
    """Clean up readiness file and connection pool when worker is shutting down."""

    READINESS_FILE.unlink(missing_ok=True)
    if _pg_pool is not None:
        _pg_pool.close()


# Always create the celery instance so it can be imported
# The config_source parameter tells Celery to look for 'aviator.configs.celeryconfig' module
celery = Celery("aviator", config_source="aviator.configs.celeryconfig")

# Add the liveness probe step
celery.steps["worker"].add(LivenessProbe)

# Configure Pub/Sub specific settings (these will override celeryconfig.py settings if needed)
if settings.broker_type == "pubsub":
    # Use the gcpubsub broker URL from settings
    if settings.broker_url:
        celery.conf.broker_url = str(settings.broker_url)

    # Explicitly disable remote control
    celery.conf.worker_enable_remote_control = False

    # Set Pub/Sub specific transport configurations following Celery docs
    celery.conf.broker_transport_options = settings.pubsub_broker_transport_options

    # Configure task serialization for Pub/Sub
    celery.conf.task_serializer = "json"
    celery.conf.result_serializer = "json"
    celery.conf.accept_content = ["json"]

    # Set emulator host if specified (for local development)
    if settings.pubsub_emulator_host:
        os.environ["PUBSUB_EMULATOR_HOST"] = settings.pubsub_emulator_host
        logger.info("Set PUBSUB_EMULATOR_HOST to %s", settings.pubsub_emulator_host)

    # Set result backend if not already configured (Pub/Sub doesn't support results by default)
    if not celery.conf.result_backend:
        logger.warning(
            "Pub/Sub broker detected but no result backend configured. Consider setting CELERY_RESULT_BACKEND."
        )

    logger.info("Celery configured for Pub/Sub with broker URL: %s", celery.conf.broker_url)

# Configure it if broker settings are available (celeryconfig.py will handle most of this)
elif settings.broker_url:
    # These overrides are needed if not using Pub/Subsu
    celery.conf.broker_url = str(settings.broker_url)
    celery.conf.task_default_queue = settings.broker_queue_name
else:
    logger.warning("No broker configuration found. Celery instance created but not configured.")

if settings.broker_consistent_hash_enabled:
    logger.info(
        "Consistent hash exchange enabled with %d worker queues (exchange: %s-hash)",
        settings.broker_worker_queue_count,
        settings.broker_queue_name,
    )


def _get_document_routing_key(request_dict: dict) -> str | None:
    """Extract a consistent routing key from the embedding request for hash-based routing.

    Uses the documentID (or document_id) from metadata so that all operations on the
    same document are routed to the same worker queue via the consistent hash exchange.
    """
    metadata = request_dict.get("metadata", {}) or {}
    return metadata.get("documentID") or metadata.get("document_id")


@celery.task(
    bind=True, base=BaseTaskWithRetry, name="aviator.celery.process_embedding_request", queue=settings.broker_queue_name
)
@tracer.start_as_current_span("process_embedding_request")
def process_embedding_request(self, request: dict, is_metadata: bool = False) -> dict:  # noqa: ANN001, ARG001
    """Process embedding request.

    !!! note
        This task handles adding, deleting, and updating documents in the vector store
        based on the provided request.

        **Operations and usage tracking:**

        - ``add``: Stores new document chunks and records an ``embedding_add`` transaction.
        - ``delete``: Removes document chunks by ``documentID`` and records an
          ``embedding_delete`` transaction.
        - ``update``: Deletes existing chunks and re-adds updated content, recorded as a
          **single** ``embedding_update`` transaction (not as separate delete + add).
          The ``chunk_count`` stored is the **net delta** (new chunks - old chunks)
          so that ``get_semantic_size`` can sum it directly.  This keeps
          ``embeddingsRequestCount``, ``chunksDeletedCount``, and
          ``documentsDeletedCount`` accurate in the usage-stats API.
    """

    request = EmbeddingRequest(**request)

    # Resolve tenant schema from request metadata
    tenant_id = request.metadata.get("tenantID") if request.metadata else None
    schema_name = tenant_id_to_schema_name(tenant_id)
    logger.info("Processing embedding request for tenant_id=%s (schema=%s)", tenant_id, schema_name)

    # Auto-create tenant schema if it doesn't exist
    if tenant_id is not None and schema_name != settings.default_schema and not tenant_service.tenant_exists(tenant_id):
        logger.info("Tenant '%s' does not exist. Creating schema '%s'...", tenant_id, schema_name)
        tenant_service.ensure_tenant(tenant_id)

    with tracer.start_as_current_span("load_embedding_extension"):
        load_embedding_extension(request=request, is_metadata=is_metadata)

    # Text splitter for regular content
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.text_splitter_chunk_size,
        chunk_overlap=settings.text_splitter_chunk_overlap,
        length_function=len,
        add_start_index=True,
    )

    # Define inner functions for add, delete, and update operations

    @tracer.start_as_current_span("process_embedding_add")
    def add() -> tuple[str, int]:
        """Add documents to vector store.

        Returns:
            A tuple of (result_message, chunk_count).

        """

        if isinstance(request.content, dict):
            request.content = json.dumps(request.content)

        # Normalize metadata keys to match those used in the vector store
        if "workspaceID" in request.metadata:
            request.metadata["workspace_id"] = request.metadata.get("workspaceID")
            request.metadata.pop("workspaceID", None)
        if "documentID" in request.metadata:
            request.metadata["document_id"] = request.metadata.get("documentID")
            request.metadata.pop("documentID", None)

        logger.debug("Storing document with metadata: %s", request.metadata)

        # Extract tables and text from content
        extracted_content = extract_tables_and_text(request.content)

        # Use OTSynopsis from metadata as fallback if both text and tables are empty
        if not extracted_content["text"] and not extracted_content["tables"]:
            fallback_synopsis = request.metadata.get("OTSynopsis", "").strip()
            if fallback_synopsis:
                extracted_content["text"] = fallback_synopsis
            else:
                logger.debug("Dropping empty content request -> %s", request)
                return "Dropped empty content", 0

        # Prepare documents for splitting
        docs = []

        # Do not split content if it is metadata driven
        if is_metadata:
            metadata = {**request.metadata, "type": "metadata", "start_index": -1}
            # Add backward compatibility keys
            if "workspace_id" in metadata and "workspaceID" not in metadata:
                metadata["workspaceID"] = metadata["workspace_id"]
            if "document_id" in metadata and "documentID" not in metadata:
                metadata["documentID"] = metadata["document_id"]
            docs = [Document(page_content=request.content, metadata=metadata, id=str(uuid7()))]
        else:
            # Process regular text content
            if extracted_content["text"]:
                logger.debug("Splitting text content for storage in vector_store")
                text_doc = Document(page_content=extracted_content["text"], metadata=request.metadata)
                text_docs = text_splitter.split_documents([text_doc])
                for doc in text_docs:
                    doc.metadata["type"] = "text"
                docs.extend(text_docs)

            # Process extracted tables separately
            if extracted_content["tables"]:
                logger.debug("Processing extracted tables for storing them in vector_store")
                try:
                    sections_to_split = [("table", TABLE_SPLITTER)]
                    html_table_splitter = HTMLSectionSplitter(headers_to_split_on=sections_to_split)
                    table_docs = html_table_splitter.split_text(extracted_content["tables"])

                    if table_docs:
                        # Filter out docs with empty or whitespace-only content
                        table_docs = [doc for doc in table_docs if doc.page_content and doc.page_content.strip()]
                        if table_docs:
                            for doc in table_docs:
                                doc.page_content = f"[TABLE]\n{doc.page_content}\n[/TABLE]"
                                doc.metadata = {**request.metadata, "type": "table"}
                            docs.extend(table_docs)
                            logger.debug("Created %d table document chunks", len(table_docs))
                except Exception:
                    logger.exception("Failed to split table content; falling back to single table document")
                    # Fallback: This will store entire tables string as a single document
                    fallback_doc = Document(
                        page_content=f"[TABLE]\n{extracted_content['tables']}\n[/TABLE]",
                        metadata={**request.metadata, "type": "table"},
                    )
                    docs.append(fallback_doc)

        logger.debug("Total document chunks created: %d", len(docs))

        # Add backward compatibility keys and cleanup metadata
        for doc in docs:
            # Add backward compatibility keys if normalized versions exist
            if "workspace_id" in doc.metadata and "workspaceID" not in doc.metadata:
                doc.metadata["workspaceID"] = doc.metadata["workspace_id"]
            if "document_id" in doc.metadata and "documentID" not in doc.metadata:
                doc.metadata["documentID"] = doc.metadata["document_id"]

            # Ensure each doc has a unique ID
            if not doc.id:
                doc.id = str(uuid7())

        # Check if documents were found, handle empty case
        if docs:
            result = []

            # ===== REMOVE EXISTING CHUNKS FROM BUFFER WITH SAME DOCUMENT_ID =====
            # Before adding new chunks, remove any buffered chunks for this document
            document_id = request.metadata.get("document_id")
            if document_id:
                with tracer.start_as_current_span("remove_buffered_chunks"):
                    removed_count = embedding_buffer.remove_by_document_id(document_id)
                    if removed_count > 0:
                        logger.debug(
                            "Removed %d existing buffered chunks for document_id: %s before adding new chunks",
                            removed_count,
                            document_id,
                        )

            with tracer.start_as_current_span("vector_store_add_documents"):
                # Delete old chunks for this document first (upsert semantics):
                # ensures re-indexing the same document_id never accumulates duplicates.
                document_id = request.metadata.get("document_id") or request.metadata.get("documentID")
                if document_id and docs:
                    vector_store.get(schema_name=schema_name).delete(filter={"document_id": document_id})
                    logger.debug("Deleted existing embeddings for document_id=%s before add", document_id)

                # Process new documents (generates embeddings)
                if docs:
                    logger.debug("Processing %d new document chunks", len(docs))
                    errors = []  # Collect error messages to allow duplicate processing to continue
                    first_error_type = None  # Track first error type for re-raising
                    for batch_idx, docs_list_part in enumerate([docs[i : i + 50] for i in range(0, len(docs), 50)]):
                        batch_start = batch_idx * 50
                        batch_hashes = None  # No hash computation needed since deduplication is disabled
                        try:
                            with tracer.start_as_current_span("vector_store_add_document"):
                                logger.debug(
                                    "Adding %d document chunks to embedding buffer (%d new docs so far).",
                                    len(docs_list_part),
                                    batch_start + len(docs_list_part),
                                )
                                result += embedding_buffer.add(docs_list_part, schema_name, batch_hashes)
                        except sqlalchemy.exc.DataError as e:
                            error_msg = f"SQL data error: {e!s}"
                            errors.append(error_msg)
                            if first_error_type is None:
                                first_error_type = sqlalchemy.exc.DataError
                            logger.error("Failed to add document batch: %s", error_msg)
                        except sqlalchemy.exc.SQLAlchemyError as e:
                            error_msg = f"Database error: {e!s}"
                            errors.append(error_msg)
                            if first_error_type is None:
                                first_error_type = sqlalchemy.exc.SQLAlchemyError
                            logger.error("Failed to add document batch: %s", error_msg)
                        except Exception as e:
                            error_msg = f"Vector store operation failed: {e!s}"
                            errors.append(error_msg)
                            if first_error_type is None:
                                first_error_type = Exception
                            logger.error("Failed to add document batch: %s", error_msg)

                    # If all batches failed, raise the first error
                    if errors and len(errors) == (len(docs) // 50 + (1 if len(docs) % 50 else 0)):
                        # All batches failed
                        error_msg = errors[0]
                        errors.clear()  # Clear to avoid holding exception references
                        if first_error_type is sqlalchemy.exc.DataError:
                            raise EmbeddingError(104, error_msg)
                        if first_error_type is sqlalchemy.exc.SQLAlchemyError:
                            raise EmbeddingRetryableError(100, error_msg)
                        raise EmbeddingRetryableError(100, error_msg)

                if result:
                    return (
                        f"Stored document successfully with {len(docs)} chunks ({len(result)} flushed, {embedding_buffer.pending_count} pending in buffer). Stored document IDs: {result}",
                        len(docs),
                    )
                else:
                    # Chunks are buffered, waiting for more data or the flush timer
                    logger.debug(
                        "Document %s buffered (%d chunks, %d chars pending). Waiting for batch threshold or timeout.",
                        request.metadata.get("document_id", "unknown"),
                        embedding_buffer.pending_count,
                        embedding_buffer.pending_chars,
                    )
                    return (
                        f"Buffered {len(docs)} chunks, waiting for batch to fill (pending: {embedding_buffer.pending_count} chunks, {embedding_buffer.pending_chars} chars).",
                        len(docs),
                    )

        logger.info("No document chunks to store in vector_store")
        return "No content chunks to store", 0

    @tracer.start_as_current_span("process_embedding_delete")
    def delete() -> tuple[str, int]:
        """Delete documents from vector store.

        Returns:
            A tuple of (result_message, chunk_count).

        """

        # Delete documents based on metadata filter
        if not request.metadata:
            logger.error("Delete operation requires metadata filter")
            raise EmbeddingError(101, "Delete operation requires metadata filter")

        # Build filter from metadata to delete matching documents
        filter_dict = {}
        if "documentID" in request.metadata:
            filter_dict["document_id"] = request.metadata["documentID"]

        if not filter_dict:
            logger.error("Delete operation requires documentID in metadata")
            raise EmbeddingError(102, "Delete operation requires documentID in metadata")

        # Count matching chunks before deletion for accurate usage tracking
        chunk_count = _count_chunks_for_filter(schema_name, filter_dict)

        try:
            # Delete documents matching the filter via the native PGVectorStore API
            store = vector_store.get(schema_name=schema_name)
            store.delete(filter=filter_dict)
        except Exception as e:
            logger.error("Error deleting embeddings: %s", e)
            raise EmbeddingRetryableError(103, f"Failed to delete documents: {e!s}")  # noqa: B904
        else:
            logger.info("Deleted embeddings with filter: %s (chunks=%d)", filter_dict, chunk_count)
            return f"Deleted {filter_dict} embeddings successfully", chunk_count

    @tracer.start_as_current_span("process_embedding_update_metadata")
    def update_metadata_only() -> str:
        """Update metadata fields on existing embeddings without re-embedding content."""
        if not request.metadata:
            raise EmbeddingError(101, "Update-metadata operation requires metadata")

        document_id = request.metadata.get("documentID")
        if not document_id:
            raise EmbeddingError(102, "Update-metadata operation requires documentID in metadata")

        # Merge the requested metadata into the existing JSONB metadata.
        # Normalize camelCase IDs to snake_case so stored keys match the rest
        # of the vector-store metadata schema.
        new_meta = dict(request.metadata)

        from psycopg import sql as psycopg_sql

        qualified_table = psycopg_sql.SQL("{}.{}").format(
            psycopg_sql.Identifier(schema_name),
            psycopg_sql.Identifier(settings.vector_store_table_name),
        )
        metadata_col = settings.vector_store_metadata_column
        meta_ident = psycopg_sql.Identifier(metadata_col)

        try:
            with _get_pg_pool().connection() as conn, conn.cursor() as cur:
                # Build SET clause dynamically based on whether workspace_id should be updated
                set_clauses = [psycopg_sql.SQL("{} = {}::jsonb || %s::jsonb").format(meta_ident, meta_ident)]
                params_list = [json.dumps(new_meta)]

                # If workspaceID exists in new_meta, also update the workspace_id column
                workspace_id_value = new_meta.get("workspaceID")
                if workspace_id_value:
                    set_clauses.append(psycopg_sql.SQL("workspace_id = %s"))
                    params_list.append(workspace_id_value)

                # Add document_id to params for WHERE clause
                params_list.append(document_id)
                set_sql = psycopg_sql.SQL(", ").join(set_clauses)

                cur.execute(
                    psycopg_sql.SQL("UPDATE {} SET {} WHERE document_id = %s").format(qualified_table, set_sql),
                    tuple(params_list),
                )
                conn.commit()
        except Exception as e:
            logger.error("Error updating metadata: %s", e)
            raise EmbeddingRetryableError(103, f"Failed to update metadata: {e!s}")  # noqa: B904

        logger.info("Updated metadata for document_id=%s", document_id)
        return f"Updated metadata for document_id={document_id} successfully"

    # Execute the requested operation.

    if request.operation == "add":
        add_message, chunk_count = add()
        result = {"add": add_message, "metadata": request.metadata}
        record_transaction_sync(
            tenant_id=tenant_id,
            transaction_type="embedding_add",
            document_count=1,
            chunk_count=chunk_count,
        )
        return result

    elif request.operation == "delete":
        delete_message, deleted_chunks = delete()
        result = {"delete": delete_message, "metadata": request.metadata}
        record_transaction_sync(
            tenant_id=tenant_id,
            transaction_type="embedding_delete",
            document_count=1,
            chunk_count=deleted_chunks,
        )
        return result

    elif request.operation == "update":
        with tracer.start_as_current_span("embedding_update"):
            if not request.content:
                # No content supplied: patch metadata in place without deleting
                # chunks or re-embedding content.
                result = update_metadata_only()
                return {"update": result, "metadata": request.metadata}
            # Content supplied: replace existing chunks by deleting and re-embedding.
            delete_result, deleted_chunks = delete()
            add_message, chunk_count = add()
            result = {
                "add": add_message,
                "delete": delete_result,
                "update": delete_result and add_message,
                "metadata": request.metadata,
            }
            # Record a single embedding_update transaction rather than separate
            # embedding_delete + embedding_add calls.  This prevents embeddingsRequestCount
            # from incrementing twice and avoids inflating chunksDeletedCount /
            # documentsDeletedCount for what is logically one replace operation.
            # chunk_count is the net delta (new - old) so that get_semantic_size
            # can sum it directly to track the actual vector store footprint.
            record_transaction_sync(
                tenant_id=tenant_id,
                transaction_type="embedding_update",
                document_count=1,
                chunk_count=chunk_count - deleted_chunks,
            )
            return result

    else:
        logger.error("Invalid operation: %s", request.operation)
        raise EmbeddingError(105, f"Invalid operation: {request.operation}")


@celery.task(
    bind=True,
    base=SummaryTaskWithRetry,
    name="aviator.celery.process_workspace_summary_request",
    queue=settings.broker_summary_queue_name,
)
@tracer.start_as_current_span("process_workspace_summary_request")
def process_workspace_summary_request(self, request: dict) -> dict:  # noqa: ANN001, ARG001
    """Process summarization request using LLM.

    Takes content from the queue and generates a summary using the configured LLM.

    Args:
        self: Celery task instance.
        request: Dictionary containing:
            - operation (str): "add", "delete", or "update" summary
            - content (str): Text content to summarize (required for add/update)
            - metadata (dict): Optional metadata (documentID, workspaceID, etc.)
            - max_tokens (int, optional): Maximum tokens for summary (default from settings)
            - temperature (float, optional): LLM temperature (default 0.3)

    Returns:
        dict: Contains summary, metadata, and any database operation results or errors.

    """

    request = WorkspaceSummary(**request)

    # Resolve tenant schema from request metadata
    tenant_id = request.metadata.get("tenantID") if request.metadata else None
    schema_name = tenant_id_to_schema_name(tenant_id)
    logger.info("Processing summary request for tenant_id=%s (schema=%s)", tenant_id, schema_name)

    # Auto-create tenant schema if it doesn't exist
    if tenant_id is not None and schema_name != settings.default_schema and not tenant_service.tenant_exists(tenant_id):
        logger.info("Tenant '%s' does not exist. Creating schema '%s'...", tenant_id, schema_name)
        tenant_service.ensure_tenant(tenant_id)

    metadata = request.metadata or {}
    document_id = metadata.get("documentID", "unknown")
    workspace_id = metadata.get("workspaceID", "unknown")

    @tracer.start_as_current_span("summary_add")
    def add() -> dict:
        """Generate and store summary."""
        content = request.content
        # Validate input
        if not content:
            logger.debug("Summarization request missing content")
            return {"error": "No content provided for summarization"}

        if isinstance(content, str) and not content.strip():
            logger.debug("Summarization request content is empty or whitespace")
            return {"error": "No content provided/empty for summarization"}

        if isinstance(content, dict) and not content:
            logger.debug("Summarization request content is an empty dict")
            return {"error": "No content provided/empty for summarization"}

        # Handle dict content
        if isinstance(content, dict):
            content = json.dumps(content)

        logger.debug(
            "Processing summarization for document_id: %s, workspace_id: %s",
            document_id,
            workspace_id,
        )

        # Generate summary
        document_summary = generate_summary(content=content)
        result = document_summary.model_dump()

        # Store summary in database
        if result and result.get("summary"):
            title = result.get("title", "").strip()
            title_embeddings = None

            # Generate embeddings for title if title exists
            if title:
                with tracer.start_as_current_span("generate_title_embeddings"):
                    try:
                        logger.debug("Generating embeddings for title: %s", title)
                        embeddings_service = EmbeddingsRegistry.get_embeddings()
                        title_embeddings_list = embeddings_service.embed_documents([title])
                        if title_embeddings_list:
                            title_embeddings = title_embeddings_list[0]
                            logger.info("Generated title embeddings with dimension: %d", len(title_embeddings))
                        else:
                            logger.warning("Embeddings service returned empty list for title")
                    except Exception as e:
                        logger.error("Failed to generate title embeddings: %s", e)
                        logger.debug("Continuing without title embeddings")

            with tracer.start_as_current_span("store_summary_data"):
                db_result = upsert_workspace_document_with_summary(
                    document_id=document_id,
                    summary_text=result["summary"],
                    title=result.get("title", ""),
                    title_embeddings=title_embeddings,
                    schema_name=schema_name,
                )
                result.update(db_result)

        return result

    @tracer.start_as_current_span("summary_delete")
    def delete() -> dict:
        """Delete summary from database."""
        # Delete documents based on metadata filter
        if not request.metadata:
            logger.error("Delete operation requires metadata filter")
            raise WorkspaceSummaryError(207, "Summary Delete operation requires metadata filter")

        # Build document_id from metadata to delete matching documents
        filter_document_id = None
        if "documentID" in request.metadata:
            filter_document_id = request.metadata["documentID"]

        if not filter_document_id:
            logger.error("Delete operation requires documentID in metadata")
            raise WorkspaceSummaryError(207, "Summary Delete operation requires documentID in metadata")

        with tracer.start_as_current_span("delete_summary_data"):
            return delete_workspace_document_summary(document_id=filter_document_id, schema_name=schema_name)

    # Execute the appropriate operation based on the request
    result = {"document_id": document_id, "workspace_id": workspace_id}

    if request.operation == "add":
        result.update(add())
        return result

    elif request.operation == "delete":
        result.update(delete())
        return result

    elif request.operation == "update":
        with tracer.start_as_current_span("summary_update"):
            result.update(add())
            return result

    else:
        logger.error("Invalid operation: %s", request.operation)
        raise WorkspaceSummaryError(207, f"Invalid operation: {request.operation}")


def extract_tables_and_text(content: str | None) -> dict[str, str]:
    """Extract HTML tables from content and return separated tables and text.

    Args:
        content: The input text content that may contain HTML tables

    Returns:
        Dictionary with 'tables' string and 'text' string with tables removed.

    """
    if not content:
        return {"tables": "", "text": ""}

    # Match outermost <table> tags, handling possible nested tables
    tables: str = ""
    text = content

    for match in TABLE_TAG_REGEX.finditer(content):
        tables += match.group(0) + "\n"
        text = text.replace(match.group(0), "")

    return {"tables": tables, "text": text.strip()}


# ── Migration tasks (optional) ────────────────────────────────────────
# Import migration consumers to register their @celery.task-decorated
# functions on this app.  Done here (after the app is fully constructed)
# rather than in celeryconfig.py to avoid circular imports during config
# finalization.
if settings.migration_enabled:
    try:
        import migration.consumer

        logger.info("Migration tasks registered on aviator worker.")
    except ImportError:
        logger.warning(
            "MIGRATION_ENABLED is true but the migration package is not installed. "
            "Migration tasks will NOT be available on this worker."
        )

    try:
        import migration.summary_consumer  # noqa: F401

        logger.info("Summary backfill tasks registered on aviator worker.")
    except ImportError:
        logger.warning(
            "MIGRATION_ENABLED is true but the migration package is not installed. "
            "Summary backfill tasks will NOT be available on this worker."
        )
