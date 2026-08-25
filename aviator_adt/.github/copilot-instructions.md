# Content Aviator ADT - AI Coding Agent Instructions

## Project Overview

Content Aviator ADT (Agent Development Toolkit) is a **LangGraph-based RAG (Retrieval-Augmented Generation) system** built with FastAPI and Celery. It's designed as an extensible platform where plugins can add custom tools, authentication handlers, and graph modifications through Python entry points.

**Architecture:** FastAPI API server + Celery worker + PostgreSQL (with pgvector) + RabbitMQ/Redis/PubSub + LangGraph agent runtime.

## Development Workflow

### Package Management - Use `uv`, not pip/poetry
- **Dependency management:** `uv sync --locked` (or task: `uv-sync-locked`)
- **Add packages:** `uv add <package>` (updates `pyproject.toml` and `uv.lock`)
- **Run commands:** `uv run <command>` (e.g., `uv run pytest`)
- Never use `pip install` directly - the project is managed via `uv`

### Running & Testing
```bash
make up              # Start all services (app + worker + db + rabbitmq)
make up-migration    # Start all services + migration producer
make down            # Stop all services
make test            # Run pytest with coverage
make check           # Format, lint (auto-fix), and run tests in one step
make ruff            # Format and lint (without fixing)
make ruff-fix        # Format and auto-fix lint issues
make docs            # Generate docs and serve with mkdocs
```

### Docker Compose Environments
- `docker-compose.yml` - Backend services only (postgres, rabbitmq)
- `docker-compose.dev.yml` - Full dev stack with debugpy on port 5678
- `docker-compose.dev.pubsub.yml` - Uses Google Pub/Sub instead of RabbitMQ
- **Migration profile:** `docker-compose.dev.yml` includes a `migration-producer` service under `profiles: [migration]`, activated via `make up-migration` (which sets `MIGRATION_ENABLED=true` for that run)

### Debugging Workflow (VS Code)
1. **Start services:** Run `make up` to spin up all containers from `docker-compose.dev.yml`
2. **Attach debugger:** Use "Docker: Attach to Aviator" launch configuration (`.vscode/launch.json`)
   - Debugpy listens on port 5678 inside container
   - Path mappings: `${workspaceFolder}/src` → `/aviator_adt/src`
3. **Alternative local debugging:**
   - "Aviator API" - Run FastAPI locally with hot reload
   - "Celery worker" - Run worker locally with `-P solo`
   - "Langgraph Studio" - Run LangGraph Studio with debug support

### Required Credentials
- **Google Service Account:** Place `otl-cs-csai.json` (or similar) in project root
- **Environment variable:** `GOOGLE_APPLICATION_CREDENTIALS=./your-credentials.json` in `.env`
- This is required for Google GenAI and Cloud services - must be obtained from team/GCP project

### Testing Requirements
- Tests run with `VECTOR_STORE=memory`, `CHECKPOINTER=memory`, and `USAGE_TRACKING_ENABLED=false` (see `tests/conftest.py`)
- Auth headers required: `{"auth-ticket": "some-valid-ticket"}`
- Use pytest fixtures from `conftest.py` for FastAPI TestClient

## API Endpoints

The system exposes a RESTful API with the following main endpoints:

### Chat & RAG (`/v1/*`)
- **POST `/v1/chat`** - Main conversational endpoint with LangGraph agent
  - Handles thread-based conversations with state persistence
  - Supports interrupts for human-in-the-loop workflows
  - **Backward-compatible chat history:** When no `thread_id` is present in `context` and `messages` contains more than one entry, the full conversation history is forwarded to the graph. When a `thread_id` exists, only the last user message is sent (the checkpointer holds prior history). Validation enforces that messages start and end with a user message and contain no consecutive same-role messages.
  - Returns: answer, references, context (thread_id)
  - Rate limited: 15/minute
- **POST `/v1/direct-chat`** - Direct LLM communication bypassing the graph
  - Invokes LLM directly without engaging default graph or RAG
  - Request: messages (required), modelName (optional), options (optional), chatID (optional)
  - Returns: LLM response text and optional chatID
  - Rate limited: 15/minute
- **POST `/v1/context`** - RAG query without chat (direct vector search)
  - Returns relevant document chunks based on query
- **GET `/v1/thread`** - Retrieve conversation history by thread_id
- **POST `/v1/feedback`** - Submit feedback on chat responses
  - Rate limited: 15/minute

### Embeddings (`/v1/*`)
- **POST `/v1/embeddings`** - Async document embedding (queued to Celery)
  - Chunks documents and stores vectors in pgvector
  - Returns 202 Accepted immediately
- **POST `/v1/metadata`** - Metadata-only embedding (no text splitting)
- **POST `/v1/direct-embed`** - Synchronous embedding generation
  - Returns vectors without storage

### WebSocket (`/v1/chat/stream`)
- **WebSocket `/v1/chat/stream`** - Real-time streaming chat interface
  - JSON message format: `{"content": "...", "thread_id": "..."}`
  - **Backward-compatible chat history:** Supports an optional `messages` array (same format as `/v1/chat`). When no `thread_id` is in `context` and `messages` contains more than one entry, the full history is forwarded to the graph. Invalid history is silently ignored and falls back to single-message mode.
  - Streams LLM responses token-by-token

### Graph Visualization
- **GET `/graph/mermaid.png`** - LangGraph state machine as Mermaid diagram
- **GET `/graph/export`** - Export graph structure

### General
- **GET `/`** - Web UI for chat interface
- **GET `/health`** - Health check endpoint
- **GET `/metrics`** - Prometheus metrics endpoint
- **GET `/docs`** - OpenAPI/Swagger documentation

All endpoints (except `/health`, `/metrics`, `/docs`) require authentication via `require_authentication()` dependency.

### Usage Stats (`/v1/usage-stats`)
- **GET `/v1/usage-stats`** - Query usage statistics for a tenant over a date range
  - Supports relative offsets (`from_offset`, `to_offset`) or absolute dates (`from_date`, `to_date`)
  - Aggregation by `units`: `days`, `months`, or `years`
  - Returns zero-filled time-series buckets with chat, embedding, and search counts
  - Includes `semanticSize` (total docs and chunks in vector store)
  - Returns 501 when `USAGE_TRACKING_ENABLED=false`
  - Rate limited: 15/minute

### Tenant Management (`/v1/tenants`)

> **Note:** These endpoints are only available when `MULTI_TENANT_ENABLED=true`.

- **POST `/v1/tenants?tenant_id=...`** - Create a new tenant schema
- **GET `/v1/tenants`** - List all tenants
- **GET `/v1/tenants/{tenant_id}`** - Get tenant info
- **DELETE `/v1/tenants/{tenant_id}`** - Delete tenant schema (CASCADE)

## Architecture Patterns

### Plugin System (Core Extension Mechanism)
The system discovers plugins via `pyproject.toml` entry points. **7 plugin types available:**

```python
[project.entry-points."aviator.auth_handlers"]       # Custom auth (e.g., OTDS, OAuth)
[project.entry-points."aviator.startup_extensions"]  # Code run at app startup
[project.entry-points."aviator.tool_modifiers"]      # Modify/add LangGraph tools
[project.entry-points."aviator.graph_extensions"]    # Modify LangGraph StateGraph
[project.entry-points."aviator.prompt_modifiers"]    # Customize prompts
[project.entry-points."aviator.rag_permission_filters"] # RAG permission filtering
[project.entry-points."aviator.routers"]             # Add FastAPI routers
[project.entry-points."aviator.celery_beat_schedules"] # Plugin-contributed Celery beat schedules
```

**Plugin loading:** See `src/aviator/plugins.py` - uses `importlib.metadata.entry_points()`
**Plugin examples:** See `docs/dev/plugins.md` for complete plugin development guide

### LangGraph Agent (`src/aviator/graph.py`)
- Main class: `ContentAviatorAgent`
- **Checkpointer:** Postgres (default) or in-memory (dev/test)
- **Tools:** RAG query, chart generation, datetime, + MCP tools via `langchain-mcp-adapters`
- **State model:** `StateModel` in `src/aviator/models.py`
- Tool modifications happen through `load_tool_modifiers()` plugin hooks

### Settings System (`src/aviator/settings.py`)
- Uses `pydantic-settings` with environment variables
- **Critical settings:**
  - `VECTOR_STORE`: "memory" or "pgvectorstore" (default)
  - `CHECKPOINTER`: "memory", "postgres", or None
  - `BROKER_TYPE`: "amqp", "redis", or "pubsub"
  - `LLM_PROVIDER`: "google_genai" or "openai"
  - `CONTENT_SYSTEM`: Determines which auth handler to load
  - `MULTI_TENANT_ENABLED`: Enable schema-per-tenant isolation (default: `true`)
  - `DEFAULT_SCHEMA`: Default PostgreSQL schema (default: `public`)
  - `TENANT_SCHEMA_PREFIX`: Prefix for tenant schemas (default: `tenant_`)
  - `TENANT_ID_PATTERN`: Regex to validate tenant IDs (default: `^[a-zA-Z0-9_-]{2,63}$`)
  - `MIGRATION_ENABLED`: Enables migration task registration and migration queue auto-subscription on worker (default: `false`)
- Settings auto-generate docs via `settings-doc` (see Makefile)

### Vector Store Pattern
- Abstraction: `VectorStoreManager` singleton in `src/aviator/vector_store/__init__.py`
- Factory: `VectorStoreFactory` creates adapters based on settings
- Usage: `from aviator.vector_store import vector_store` then `vector_store.get()`

### Celery Worker (`src/aviator/celery.py`)
- Task: `process_embedding_request` - handles document chunking and embedding
- Task: `cleanup_usage_transactions` - periodic cleanup of old usage tracking detail rows
- Task: `cleanup_checkpoints` - periodic cleanup of old LangGraph checkpoint rows
- **Migration tasks:** When `MIGRATION_ENABLED=true`, imports `migration.consumer` to register migration tasks (`process_migration_batch`, `rebuild_schema_indexes_task`, `shutdown_workers`) on the same Celery app — no separate migration worker needed
- **Tenant resolution:** Reads `tenantID` from `request.metadata` (not from user dict)
- **Auto-create:** If `MULTI_TENANT_ENABLED=true` and the tenant schema doesn't exist, the worker auto-creates it
- **Liveness/Readiness probes:** Uses temp files (`/tmp/worker_heartbeat`, `/tmp/worker_ready`)
- **Retry pattern:** `BaseTaskWithRetry` with exponential backoff (max 7 retries)
- Splitter: `RecursiveCharacterTextSplitter` with custom `TABLE_SPLITTER = "<TABLE>"`
- **Beat schedule:** `cleanup-usage-transactions` runs daily at 02:00 UTC
- **Beat schedule:** `cleanup-checkpoints` runs daily at 03:00 UTC (retention controlled by `CHECKPOINTER_RETENTION_HOURS`, default `720`)
- **Queue auto-detection:** `entrypoint.sh` automatically adds the `csai-adt-migration` queue when `MIGRATION_ENABLED=true`

### Usage Tracking (`src/aviator/services/usage_tracking/`)
- Records every API transaction (chat, chat_ws, direct-chat, embedding, search) per tenant
- **ORM layer:** `orm.py` — SQLAlchemy declarative models (`Base`, `TenantSchemaMap`, `UsageTransaction`, `UsageDailyTally`)
- **DB layer:** `db.py` — `UsageTrackingDB` singleton managing async + sync SQLAlchemy engines with `schema_translate_map` for multi-tenant routing
- **Recorder:** `recorder.py` — `record_transaction()` (async) and `record_transaction_sync()` (sync for Celery); fire-and-forget, errors never propagate; aggregates daily tallies in application code (no database triggers)
- **Queries:** `queries.py` — `get_usage_stats()` reads tallies via SQLAlchemy ORM grouped by day/month/year (aggregation in Python); `get_semantic_size()` computes cumulative document/chunk totals (adds + updates − deletes)
- **Cleanup:** `cleanup.py` — batch-deletes detail rows older than `usage_tracking_retention_days` and tally rows older than `usage_tracking_tally_retention_days`
- **Models:** `models.py` — `StatsQueryParams`, `DailyTallyModel`, `SemanticSizeModel`, `StatsResponseModel`
- **Metrics:** `metrics.py` — Prometheus counter (`aviator_usage_transactions_total`) and gauges (`aviator_semantic_documents_total`, `aviator_semantic_chunks_total`)
- **Settings:** `USAGE_TRACKING_ENABLED`, `USAGE_TRACKING_MAX_QUERY_DAYS`, `USAGE_TRACKING_RETENTION_DAYS`, `USAGE_TRACKING_TALLY_RETENTION_DAYS`, `USAGE_TRACKING_POOL_SIZE`

### LLM Registry (`src/aviator/services/llm.py`)
- Factory pattern: `LLMRegistry.get_llm(assistant=True/False)` 
- Supports Google GenAI and OpenAI providers
- Two model types: base model and assistant model (configured separately)

## Code Conventions

### Database Queries — Use SQLAlchemy Core, not raw SQL
- **Always** use SQLAlchemy Core constructs (`select()`, `func.count()`, `Table.c.*`) for database queries
- Avoid `text()` with f-strings or string interpolation — use parameterised `text()` only for queries that cannot be expressed with Core (e.g. `information_schema` lookups)
- JSONB field access: use `column["key"].astext` instead of raw `->>'key'` operators

### Models & Validation
- All models use Pydantic BaseModel (v2) with strict validation
- See `src/aviator/models.py` for canonical examples:
  - `StateModel` - LangGraph state
  - `EmbeddingRequest` - Document embedding payload
  - `ChatRequestModel/ChatResponseModel` - Chat API models
- Tool-specific models in tool files (e.g., `RAGQueryModel` in `tools/rag.py`)

### API Structure
- Main app: `src/aviator/main.py` with OpenTelemetry instrumentation
- v1 endpoints: `src/aviator/api/v1.py` 
- Auth middleware: `src/aviator/api/auth.py` - uses `require_authentication()` dependency
- Rate limiting via `slowapi` - limiter defined in `utils/limiter.py`

### Error Handling
- Custom exceptions in `src/aviator/exceptions.py`:
  - `EmbeddingError` - General embedding failures
  - `EmbeddingRetryableError` - Transient errors (triggers Celery retry)
  - `TenantError` / `TenantNotFoundError` / `TenantAlreadyExistsError` - Tenant management errors

### Logging & Observability
- OpenTelemetry instrumentation: FastAPI, Google GenAI, psycopg, requests
- Prometheus metrics via `prometheus-fastapi-instrumentator`
- Custom socket counter: `aviator.metrics.socket_counter`
- Langfuse for LLM tracing: `aviator.utils.langfuse._callbacks`

## File Organization Patterns

```
src/aviator/
├── main.py              # FastAPI app, lifespan, instrumentation
├── graph.py             # ContentAviatorAgent, LangGraph setup
├── celery.py            # Background tasks, embeddings worker, optional migration task registration
├── settings.py          # Pydantic settings, env config
├── plugins.py           # Plugin discovery & loading
├── models.py            # Pydantic models for API & state
├── api/                 # FastAPI routers
│   ├── v1.py           # v1 endpoints (embeddings, RAG, chat)
│   ├── stats.py        # Usage stats endpoint (/v1/usage-stats)
│   ├── auth.py         # Authentication dependencies
│   ├── tenants.py      # Tenant CRUD endpoints
│   └── websockets.py   # WebSocket handlers
├── services/           # Service layer abstractions
│   ├── llm.py          # LLM provider registry
│   ├── embeddings.py   # Embeddings provider registry
│   ├── tenant.py       # Tenant schema management (CRUD)
│   └── usage_tracking/ # Usage stats recording & querying
│       ├── orm.py      # SQLAlchemy ORM models (Base, tables)
│       ├── db.py       # SQLAlchemy engine management, sessions, tenant schemas
│       ├── models.py   # Pydantic models for stats API
│       ├── queries.py  # Read path (tallies + semantic size)
│       ├── recorder.py # Write path (async + sync recorders + tally upsert)
│       ├── cleanup.py  # Retention cleanup (Celery beat task)
│       └── metrics.py  # Prometheus counters & gauges
├── tools/              # LangGraph tools
│   ├── rag.py          # RAG query tool
│   ├── chart_generator.py
│   └── date_time.py
└── vector_store/       # Vector store abstraction
    ├── __init__.py     # VectorStoreManager singleton (tenant-aware)
    ├── factory.py      # Adapter factory (schema_name support)
    └── adapters/       # Store implementations
```

```
src/migration/                  # CSAI → ADT pgvector migration module
├── settings.py               # MigrationSettings (env_prefix="MIGRATION_")
├── consumer.py               # Celery tasks registered on aviator.celery app
├── producer.py               # Reads source DB, publishes batches to queue (with retry)
├── run_producer.py           # CLI entrypoint with lightweight Celery client
├── schema_manager.py         # Lazy tenant schema creation
├── checkpoint.py             # migration_state tracking
├── preflight.py              # Pre-flight checks
├── index_manager.py          # Drop / rebuild / status for target indexes
└── verify.py                 # Post-migration verify (report / repair)
```

## Helm Deployment

- Chart location: `helm/aviator/`
- Site-specific values: `helm/site-specific-values/{environment}.yaml`
- Backend chart: `helm/aviator-backend/` (includes pgvector, rabbitmq, pubsub subcharts)
- **Migration Job:** Set `migration.enabled=true` in Helm values to create a K8s Job for the migration producer and configure the worker to consume migration tasks. See `helm/aviator/templates/job-migration.yaml`.

## Common Pitfalls

1. **Don't bypass `uv`** - Always use `uv run` for commands, not direct Python
2. **Plugin discovery requires entry points** - Plugins won't load without `pyproject.toml` entries
3. **Tests need memory backends** - Set `VECTOR_STORE=memory` and `CHECKPOINTER=memory` in test env
4. **Auth is pluggable** - The auth handler is loaded based on `CONTENT_SYSTEM` setting
5. **Celery vs. sync** - Embedding requests go to Celery unless `VECTOR_STORE=memory`
6. **LangGraph state** - Always work with `StateModel` for graph state, not raw dicts
7. **Multi-stage Docker builds** - Base image pattern means plugins extend, not replace
8. **Use SQLAlchemy Core** - Never use raw SQL with f-string interpolation; use `Table`, `select()`, `func.*`, and JSONB `column["key"].astext` accessors

## Documentation

- Docs source: `docs/` directory
- Generate API docs: `uv run python -m aviator openapi`
- Generate graph visualization: `uv run python -m aviator draw`
- Full doc site: `make docs` (serves on localhost)
- Live documentation: https://aviator-adt-f912d2.glpages.otxlab.net/

## Merge Request Description Template

When asked to generate an MR description, always follow this exact format.

- Use a copyable Markdown code block so the output can be pasted directly into GitLab.
- Do not generate VS Code-specific links (for example, `vscode://...`). Use normal GitLab-compatible URLs only.
- Keep the Problem Description concise and avoid listing every changed file.
- In Test Results, include how many tests passed.
- Keep `VE Ticket URL` exactly as written in the heading.
- In QA Instructions, include concrete reproduction/validation steps only when they are known with confidence; otherwise write `_TBD_`.

Use this template:

```markdown
## VE Ticket URL:
_Link to the relevant ticket or issue._

## Problem Description:
Briefly describe the issue or feature request. Provide any relevant context or background.

## Approach:
Summarize the changes made in this MR. Mention key decisions, design choices, or any notable implementation details.

## Test Results:
- Include the result of all of the test cases (integration + unit).

## QA Instructions:
Provide clear steps for QA to verify this MR (either here or in the VE ticket):
1. Environment setup steps (if needed)
2. Data requirements
3. Specific scenarios or behaviors to validate
4. Include any updated prompts.
```
