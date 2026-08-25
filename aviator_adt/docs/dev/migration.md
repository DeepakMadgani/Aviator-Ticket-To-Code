# CSAI → ADT Vector Store Migration

Migrates pgvector embeddings from the **CSAI** (`llm` repo) database to the **ADT** (`aviator_adt`) database, routing rows into tenant-specific schemas. Migration tasks run on the **main aviator Celery worker** — no separate migration worker container is needed. Embeddings are copied as-is (no re-embedding).

---

## Quick Start

### 1. Set migration variables in the root `.env`

All migration-specific settings use the `MIGRATION_` prefix:

```dotenv
MIGRATION_SOURCE_DSN=postgresql://user:pass@csai-host:5432/CSAI
# Worker toggle (needed for manual worker/compose runs)
# MIGRATION_ENABLED=true
# Optional overrides (defaults shown)
# MIGRATION_SOURCE_TABLE=testlangchain
# MIGRATION_BATCH_SIZE=500
# MIGRATION_DRY_RUN=false
```

> **Note:** Target database, broker, and vector store settings (table name, schema
> prefix, vector size, etc.) are read from the main ADT settings via
> `POSTGRES_CONNECTION`, `BROKER_URL`, etc. These are set in
> `docker-compose.dev.yml` or the root `.env` file — no need to duplicate them.
>
> **Important:** Both the producer AND workers need `MIGRATION_SOURCE_DSN` since
> workers fetch rows directly from the source database.

### 2. Run with Docker Compose (from repo root)

```bash
make up-migration
```

Or equivalently:

```bash
MIGRATION_ENABLED=true docker compose -f docker-compose.dev.yml --profile migration up -d --build
```

`make up-migration` injects `MIGRATION_ENABLED=true` for that compose command.

This starts:
- **PostgreSQL** + **RabbitMQ** (from `docker-compose.dev.yml`)
- **Aviator worker** — handles both embedding and migration tasks (fetches from source DB)
- **Migration producer** — reads all IDs, publishes ID batches (run-once, via `migration` profile)

When `MIGRATION_ENABLED=true`, the aviator worker automatically listens on the `csai-adt-migration` queue in addition to its default queue.

### 3. Run locally (development)

```bash
# Install dependencies (from repo root)
uv sync --locked

# Start the aviator worker with both queues (in one terminal)
# Note: Workers need MIGRATION_SOURCE_DSN to fetch rows from source DB
MIGRATION_ENABLED=true MIGRATION_SOURCE_DSN=postgresql://... celery -A aviator.celery worker --loglevel=info -Q aviator-embeddings,csai-adt-migration

# Run the producer (in another terminal)
MIGRATION_SOURCE_DSN=postgresql://... python -m migration.run_producer
```

---

## Architecture

```
┌──────────────────────┐       ┌──────────────┐       ┌──────────────────────┐
│  Migration Producer  │──────▶│ Message Queue│──────▶│   Aviator Worker     │
│  (reads all IDs,     │       │ (RabbitMQ /  │       │  (fetches rows by ID │
│   publishes batches) │       │  PubSub)     │       │   writes to target)  │
└──────────────────────┘       └──────────────┘       └──────────────────────┘
         │                                                      │
         │ SELECT id ORDER BY id                                │ SELECT ... WHERE id IN (...)
         ▼ (paginated)                                          ▼
┌──────────────────────┐                               ┌──────────────────────┐
│    Source DB         │                               │    Source DB         │
│    (CSAI)            │                               │    (CSAI)            │
└──────────────────────┘                               └──────────────────────┘
```

### ID-Based Architecture

The migration uses an **ID-based approach** where:

1. **Producer** reads all row IDs from the source table (paginated, ID-only queries)
2. **Producer** batches IDs by `batch_size` and publishes each batch: `["id-1", "id-2", ...]`, `["id-501", "id-502", ...]`, ...
3. **Workers** receive ID batches and fetch full rows from the source DB using `WHERE id IN (...)`
4. **Workers** group rows by tenant schema and insert into the target DB

This design keeps queue messages small (just ID strings, no embeddings), is deterministic (each batch targets specific IDs regardless of concurrent changes), and distributes the source DB read load across multiple workers.

### Producer

- Reads all row IDs from the source table (paginated queries, ID column only).
- Batches IDs and publishes to the message queue (default batch_size: 500).
- Tracks progress persistently in the target DB (see Checkpoint section).
- On completion, enqueues an **index rebuild** task (if `DEFER_INDEXES=true`) followed by a **worker shutdown** task.

### Consumer (Aviator Celery worker)

- Migration tasks are registered on the **main aviator Celery app** (`aviator.celery`). When `MIGRATION_ENABLED=true`, `celery.py` imports `migration.consumer` to register the task functions.
- The aviator worker automatically adds the `csai-adt-migration` queue when `MIGRATION_ENABLED=true` is detected (via `entrypoint.sh`).
- Receives ID batches from the queue.
- **Connects to the source DB** and fetches full rows using `SELECT ... WHERE id IN (...)`.
- Groups fetched rows by `tenantID` in metadata → target schema.
- Ensures each target schema + table exists (lazy init via langchain's `PGEngine`, cached per process).
- Inserts into the correct tenant schema via direct SQL (`INSERT INTO {schema}.{table}`).
- Uses `ON CONFLICT (langchain_id) DO NOTHING` for idempotency.

### Finalization

After the producer publishes all batches, it enqueues two final tasks into the **same queue** (FIFO ordering guarantees they run after all batch tasks):

1. **`rebuild_indexes`** — Rebuilds HNSW, GIN, and BTREE indexes on all target schemas (only if `MIGRATION_DEFER_INDEXES=true`; indexes are skipped during migration for dramatically faster ingestion).
2. **`shutdown_workers`** — Logs migration completion. Workers continue running (they are shared with the main aviator workload).

---

## Multi-Tenancy

CSAI stores everything in a **single table** (`public` schema). Tenant isolation is done via `metadata->>'tenantID'` JSONB filtering.

ADT uses **schema-level isolation**: each tenant gets a dedicated PostgreSQL schema (`tenant_{tenantId}`) with its own table. Records without `tenantID` in metadata go to the configured `default_schema` (default: `public`).

### Migration Logic per Row

```
tenant_id = row.metadata->>'tenantID'

if tenant_id is NULL or empty:
    target_schema = settings.default_schema        # e.g. "public"
else:
    target_schema = "{settings.tenant_schema_prefix}{tenant_id}"  # e.g. "tenant_acme"
```

The migration service **auto-creates** target tenant schemas (+ table + indexes) on first encounter, using the shared `aviator.vector_store.schema` module — the same source of truth used by the main ADT application and `TenantService.create_tenant()`.

---

## Schema Mapping

| Source (CSAI)  | Type       | Target (ADT)          | Type   | Notes                                                |
|----------------|------------|-----------------------|--------|------------------------------------------------------|
| `id`           | uuid PK    | `langchain_id`        | uuid   | Copy as-is                                           |
| `text`         | text       | `content`             | text   | Copy as-is                                           |
| `metadata`     | jsonb      | `langchain_metadata`  | jsonb  | Copy as-is                                           |
| `embedding`    | vector(N)  | `embedding`           | vector | Copy as-is — dimensions must match                   |
| —              | —          | `workspace_id`        | text   | Extract `metadata->>'workspaceID'`                   |
| —              | —          | `document_id`         | text   | Extract `metadata->>'documentID'`                    |

> **Source metadata key casing:** `workspaceID`, `documentID`, `tenantID` (capital D in all three).

---

## Configuration

### Migration-specific settings (set in root `.env`)

All migration variables use the `MIGRATION_` prefix (via `pydantic-settings` `env_prefix`):

| Variable                          | Default              | Description                                            |
|-----------------------------------|----------------------|--------------------------------------------------------|
| `MIGRATION_SOURCE_DSN`            | —                    | Source PostgreSQL connection string (CSAI) **required by producer AND workers** |
| `MIGRATION_SOURCE_TABLE`          | `testlangchain`      | Source table name                                      |
| `MIGRATION_SOURCE_SCHEMA`         | `public`             | Source PostgreSQL schema                               |
| `MIGRATION_BATCH_SIZE`            | `500`                | Rows per (offset, limit) batch — workers fetch this many rows per task |
| `MIGRATION_MIGRATION_QUEUE`       | `csai-adt-migration` | Queue / topic name                                     |
| `MIGRATION_STRIP_TENANT_FROM_META`| `false`              | Remove `tenantID` key from target metadata             |
| `MIGRATION_DRY_RUN`               | `false`              | Log what would be done without writing                 |
| `MIGRATION_DEFER_INDEXES`         | `true`               | Skip indexes during insert; rebuild after              |
| `MIGRATION_LOG_LEVEL`             | `INFO`               | Logging level                                          |

> **Note:** Both the producer and workers need access to `MIGRATION_SOURCE_DSN` since workers fetch rows directly from the source database using the `(offset, limit)` tuples published by the producer.

### Target settings (delegated to ADT `aviator.settings`)

Target database, broker, and vector store settings are **not duplicated** in the migration config. They are read at runtime from `aviator.settings.Settings` — the same settings class the main ADT application uses. This ensures a single source of truth.

In Docker Compose these are passed via `environment:` in `docker-compose.dev.yml`:

| Variable                       | Description                                    |
|--------------------------------|------------------------------------------------|
| `POSTGRES_CONNECTION`          | Target PostgreSQL connection string (ADT)      |
| `BROKER_URL`                   | Message broker URL (RabbitMQ / Pub/Sub)         |
| `MIGRATION_ENABLED`            | Enables migration task registration and migration queue auto-subscription on worker |
| `VECTOR_STORE_TABLE_NAME`      | Target table name (default: `aviator`)          |
| `DEFAULT_SCHEMA`               | Default schema for non-tenant rows              |
| `TENANT_SCHEMA_PREFIX`         | Prefix for tenant schemas (default: `tenant_`)  |
| `VECTOR_SIZE`                  | Embedding dimension (default per LLM provider)  |
| `VECTOR_STORE_METADATA_COLUMN` | JSONB metadata column name                      |

---

## Pre-flight Checks

Before migration starts, the producer must verify:

1. **Source DB is reachable** and `SOURCE_TABLE` exists.
2. **Target DB is reachable**.
3. **Vector dimensions match** between source and target (query `pg_attribute.atttypmod` for the `embedding` column on both sides).
4. **Migration not already completed** (check `migration_state`).

Fail fast with a clear error if any check fails.

---

## Checkpoint / Resume

Progress is tracked in the **target database** using a dedicated `migration_state` table in the `public` schema:

```sql
CREATE TABLE IF NOT EXISTS migration_state (
    id          TEXT PRIMARY KEY DEFAULT 'csai_to_adt',
    last_id     UUID,            -- last successfully migrated source row id
    status      TEXT NOT NULL,    -- 'in_progress', 'completed'
    total_rows  BIGINT,          -- total source row count (set once at start)
    migrated    BIGINT DEFAULT 0,-- rows migrated so far
    started_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
```

- Producer queries `SELECT last_id, status FROM migration_state`.
- If `status = 'completed'`, exit immediately.
- If `last_id` exists, resume with `WHERE id > last_id ORDER BY id`.
- After each batch is published, update `last_id` and `migrated` count.
- `ON CONFLICT (langchain_id) DO NOTHING` on the consumer side ensures duplicate batches are harmless.
- The producer retries transient source DB errors (up to 5 attempts with exponential backoff) before failing, and **never** marks the migration as completed unless all rows have been published.

> **Note:** Source rows must be read in a deterministic order (`ORDER BY id`) for checkpoint to work correctly. UUID v4 ordering is arbitrary but stable, which is sufficient.

---

## Fault Tolerance

- **Producer container** runs with `restart: on-failure`. If a transient error (e.g. source DB connection drop) crashes the producer, Docker restarts it automatically. The checkpoint ensures it resumes from the last committed batch.
- A **30-second backoff** is applied before exiting on error to prevent rapid restart loops.
- **Consumer workers** run with `restart: on-failure` and Celery's built-in retry with exponential backoff (up to 7 retries per task).
- All operations are **idempotent**: `ON CONFLICT DO NOTHING` on inserts, `CREATE SCHEMA IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`.

---

## Post-Migration Verification

After the producer and consumer finish, run the **verify** module to confirm all source rows reached the target database. Verification filters on `langchain_metadata->>'_source' = 'csai_legacy'` so it only counts migrated rows.

### Report (count-based, fast)

Compares the source row count against the total number of `csai_legacy`-tagged rows across all target schemas:

```bash
uv run python -m migration.verify report
```

- Exit code `0` → counts match, migration is complete.
- Exit code `1` → rows are missing.

### Repair (ID-level diff + re-publish)

Performs a full ID-level diff between source and target, then re-publishes any missing rows to the migration queue:

```bash
uv run python -m migration.verify repair
```

The consumer's `ON CONFLICT DO NOTHING` makes repair fully idempotent — running it multiple times is safe.

### Recommended workflow

1. Run migration: `make up-migration`
2. Wait for producer + consumer to finish
3. `uv run python -m migration.verify report` — check for gaps
4. If gaps exist: `uv run python -m migration.verify repair`
5. Repeat steps 3–4 until report shows 0 missing

---

## Shared Schema Utilities

Table and index DDL is defined once in `src/aviator/vector_store/schema.py` and shared between the main ADT application and the migration service:

- **`init_vector_table()`** — Creates the vector store table using langchain's `PGEngine.init_vectorstore_table()` with the correct `vector_size`, metadata columns, and schema name.
- **`generate_index_ddl()` / `create_indexes()`** — Generates and executes `CREATE INDEX IF NOT EXISTS` DDL for HNSW (embedding), GIN (metadata JSONB), and BTREE (workspace_id, document_id) indexes.

Both `PGVectorStoreAdapter` (main app) and `schema_manager.ensure_schema()` (migration) import from this module.

---

## Docker Compose

The migration runs from the **repo root** using a Compose profile:

```bash
make up-migration
# or equivalently:
MIGRATION_ENABLED=true docker compose -f docker-compose.dev.yml --profile migration up -d --build
```

`make up-migration` injects `MIGRATION_ENABLED=true` for that compose command.

- `docker-compose.dev.yml` provides `db` (pgvector), `rabbitmq`, and the `aviator-worker` services.
- The `migration-producer` service is defined in the same file with `profiles: [migration]` — it only starts when the `migration` profile is activated.
- The **aviator worker** automatically adds the `csai-adt-migration` queue when `MIGRATION_ENABLED=true` is set in the environment (auto-detected in `entrypoint.sh`).
- Migration source code lives under `src/migration/` alongside `src/aviator/`, so the worker can import `migration.consumer` directly.

---

## Kubernetes Deployment

For Kubernetes environments (Docker Desktop, GKE, etc.) the migration runs as a **Helm-managed Job**. Setting `migration.enabled=true` in Helm values creates the migration resources alongside the normal deployment — no separate profiles or manual steps required.

### How It Works

When `migration.enabled=true`:

1. `MIGRATION_ENABLED=true` is injected into the shared **ConfigMap**, so the worker registers migration consumer tasks and subscribes to the `csai-adt-migration` queue.
2. **Migration config and secrets** (source DB credentials) are added to the shared ConfigMap and Secrets, so workers can connect to the source DB.
3. A Kubernetes **Job** (`aviator-migration`) is created that runs the migration producer. It counts rows, publishes `(offset, limit)` batches to the queue, and exits once all batches are published.
4. **Workers** receive batches, fetch rows directly from the source DB, and write to the target DB.

When `migration.enabled=false` (the default), no migration resources are created and the worker operates normally.

### Quick Start (Docker Desktop)

```bash
# 1. Deploy backend services
skaffold -f ./skaffold-backend.yaml run -p docker-desktop

# 2. Deploy Aviator with migration enabled
NO_BUILD=1 skaffold run -p docker-desktop \
  --set migration.enabled=true \
  --set 'migration.secrets.items.MIGRATION_SOURCE_DSN=postgresql://user:pass@source-host:5432/CSAI'
```

Or use a values file:

```yaml
# helm/site-specific-values/migration.yaml
migration:
  enabled: true
  secrets:
    items:
      MIGRATION_SOURCE_DSN: "postgresql://user:pass@source-host:5432/CSAI"
  config:
    MIGRATION_BATCH_SIZE: "500"
    # MIGRATION_DRY_RUN: "true"
```

### GCP Production Deployment

In GCP environments with **Cloud SQL Proxy** and **Google Secret Manager**, the migration Job works alongside the existing infrastructure with no extra configuration beyond enabling migration.

#### How It Works

- **Target database password**: When the shared ConfigMap includes `SECRETS_MANAGER=google` and `SECRETS_PGVECTOR_PASSWORD_KEY`, the migration producer resolves the target database password from Google Secret Manager at startup (via `aviator.settings.Settings.model_post_init()`). No `POSTGRES_PASSWORD` env var is needed.
- **Source database password**: Instead of providing a full `MIGRATION_SOURCE_DSN` with an embedded password, you can set individual source components (`MIGRATION_SOURCE_HOST`, `MIGRATION_SOURCE_USER`, etc.) and `MIGRATION_SOURCE_PASSWORD_KEY` to resolve the source password from Secret Manager. This avoids hardcoding credentials.
- **Cloud SQL Proxy sidecar**: When `cloudsqlproxy.enabled=true`, the migration Job automatically includes a Cloud SQL Proxy sidecar with the `--quitquitquit` flag. After the producer finishes, the main container signals the proxy to shut down via its admin endpoint (`cloudsqlproxy.quitquitUrl`) so the Job pod can terminate cleanly.
- **Workload Identity**: When `gcp.workloadIdentity.enabled=true`, the Job uses the worker's Kubernetes service account (annotated for Workload Identity) instead of mounting a GSA credentials file. This provides both Secret Manager access and Cloud SQL IAM authentication.

#### Example Values (GCP with Cloud SQL + Secret Manager)

```yaml
# In your site-specific values file (e.g., gcp-production.yaml)
config:
  SECRETS_MANAGER: "google"
  SECRETS_PGVECTOR_PASSWORD_KEY: "projects/<project>/secrets/target-db-password/versions/latest"
  POSTGRES_HOST: "127.0.0.1"   # Cloud SQL Proxy listens on localhost
  POSTGRES_USER: "aviator"
  POSTGRES_PORT: "5432"

cloudsqlproxy:
  enabled: true
  # Optional: override if Cloud SQL Proxy admin bind/port differs from default
  quitquitUrl: "http://localhost:9091/quitquitquit"
  instanceConnectionName: "project:region:instance"

gcp:
  workloadIdentity:
    enabled: true

migration:
  enabled: true
  config:
    MIGRATION_BATCH_SIZE: "500"
    # Source DB components — password resolved from Secret Manager
    MIGRATION_SOURCE_HOST: "source-db-host"
    MIGRATION_SOURCE_USER: "postgres"
    MIGRATION_SOURCE_DATABASE: "CSAI"
    MIGRATION_SOURCE_PASSWORD_KEY: "projects/<project>/secrets/source-db-password/versions/latest"
```

Alternatively, if you prefer to use a full source DSN (e.g. when the source password is not in Secret Manager):

```yaml
migration:
  enabled: true
  secrets:
    items:
      MIGRATION_SOURCE_DSN: "postgresql://user:pass@source-host:5432/CSAI"
```

The migration Job inherits `SECRETS_MANAGER`, `SECRETS_PGVECTOR_PASSWORD_KEY`, `POSTGRES_HOST`, etc. from the shared ConfigMap, so the target DSN is constructed automatically.

### Helm Values Reference

**Job settings** (`migration.*`):

| Value | Default | Description |
|-------|---------|-------------|
| `migration.enabled` | `false` | Create migration Job + configure worker |
| `migration.backoffLimit` | `3` | Job retries before failure |
| `migration.ttlSecondsAfterFinished` | `86400` | Auto-delete Job after 24h |
| `migration.activeDeadlineSeconds` | — | Max runtime (no limit) |
| `migration.image.*` | (falls back to default image) | Override container image |
| `migration.resources` | 512Mi / 1Gi | CPU and memory limits |

**Non-sensitive config** (`migration.config.*`):

These are added to the shared ConfigMap and available to both producer and workers.

| Key | Description |
|-----|-------------|
| `MIGRATION_SOURCE_TABLE` | Source table name |
| `MIGRATION_SOURCE_SCHEMA` | Source PostgreSQL schema |
| `MIGRATION_BATCH_SIZE` | Rows per (offset, limit) batch message |
| `MIGRATION_DRY_RUN` | Log without writing |
| `MIGRATION_DEFER_INDEXES` | Skip indexes during insert |
| `MIGRATION_LOG_LEVEL` | Logging level |
| `MIGRATION_STRIP_TENANT_FROM_META` | Remove tenantID from metadata |

**Source connection** — provide **either** a full DSN **or** individual components:

| Key | Default | Description |
|-----|---------|-------------|
| `MIGRATION_SOURCE_DSN` | — | Full source connection string (takes precedence) |
| `MIGRATION_SOURCE_HOST` | — | Source hostname (used when DSN is not set) |
| `MIGRATION_SOURCE_PORT` | `5432` | Source port |
| `MIGRATION_SOURCE_USER` | `postgres` | Source user |
| `MIGRATION_SOURCE_DATABASE` | `postgres` | Source database name |
| `MIGRATION_SOURCE_PASSWORD_KEY` | — | Secret Manager key for source password |

**Secrets** (`migration.secrets.items.*`):

These are added to the migration Secret and mounted to both producer and workers.

| Key | Description |
|-----|-------------|
| `MIGRATION_SOURCE_DSN` | Full source DSN (when not using individual components) |
| `MIGRATION_SOURCE_PASSWORD` | Source password (when not using Secret Manager) |

### Monitoring

```bash
# Watch the Job
kubectl get jobs -l app=aviator-migration --watch

# Stream producer logs
kubectl logs job/aviator-migration -f

# Check worker is consuming migration tasks
kubectl logs deployment/aviator-worker | grep migration
```

### Post-Migration

After the migration Job completes, redeploy without `migration.enabled` to remove migration resources:

```bash
NO_BUILD=1 skaffold run -p docker-desktop
```

---

## File Structure

```
aviator_adt/
├── docker-compose.dev.yml            # migration-producer defined with profiles: [migration]
├── Dockerfile                        # Main image — includes migration source under src/
├── helm/
│   └── aviator/
│       └── templates/
│           └── job-migration.yaml    # K8s Job template (conditional on migration.enabled)
├── src/
│   ├── aviator/
│   │   ├── celery.py                 # Conditionally imports migration.consumer when MIGRATION_ENABLED=true
│   │   └── vector_store/
│   │       └── schema.py             # Shared DDL: table init + index creation
│   └── migration/
│       ├── __init__.py
│       ├── settings.py               # MigrationSettings (env_prefix="MIGRATION_") — delegates target config to aviator.settings
│       ├── models.py                 # Pydantic models for batch messages
│       ├── producer.py               # Reads source DB, publishes to queue
│       ├── consumer.py               # Celery tasks registered on aviator.celery app
│       ├── schema_manager.py         # Lazy tenant schema creation (uses aviator.vector_store.schema)
│       ├── checkpoint.py             # migration_state read/write (accepts optional conn for reuse)
│       ├── preflight.py              # Pre-flight checks
│       ├── index_manager.py          # Drop / rebuild / status CLI for target indexes
│       ├── run_producer.py           # CLI entrypoint — lightweight Celery client for send_task()
│       ├── summary_producer.py       # Discovers docs in source, aggregates chunks, publishes summary batches
│       ├── summary_consumer.py       # Celery tasks: generates LLM summaries, stores in target DB
│       └── run_summary_producer.py   # CLI entrypoint for summary backfill producer
└── tests/
    └── migration/
        └── ...
```

---

## Summary Migration (Document Summary Backfill)

After the chunk migration is complete, a **summary backfill** job generates document summaries for all migrated documents. The old CSAI system did not store summaries — they must be generated via LLM as a post-migration step.

### How It Works

```
┌──────────────────────┐       ┌──────────────┐       ┌──────────────────────┐
│  Summary Producer    │──────▶│ Message Queue │──────▶│  Summary Worker      │
│  (reads source DB,   │       │ (RabbitMQ /   │       │  (generates summary  │
│   aggregates chunks) │       │  PubSub)      │       │   via LLM, stores)   │
└──────────────────────┘       └──────────────┘       └──────────────────────┘
```

1. **Discovery**: The producer pages through distinct documents in the source table (`metadata->>'documentID'`), grouped by tenant.
2. **Filtering**: Documents that already have summaries in the target `workspace_document_summaries` table are skipped.
3. **Chunk aggregation**: For each document, all source chunks are read and joined (ordered by `metadata->'loc'->'lines'->>'from'`).
4. **Publishing**: Aggregated documents are batched into `SummaryBatch` messages (default 10 docs/batch) and sent to the `csai-adt-summary` queue.
5. **Summary generation**: The consumer calls `generate_summary()` which invokes the configured LLM to produce a title and summary, then generates title embeddings and stores via `upsert_workspace_document_with_summary()`.
6. **Checkpoint**: Progress is tracked in the `migration_state` table with `id = 'summary_backfill'` (separate from the chunk migration's `csai_to_adt` checkpoint).

### Prerequisites

- Chunk migration **must** be completed first (all source rows in the target DB).
- LLM credentials must be configured (`LLM_PROVIDER`, `LLM_MODEL`, Google/OpenAI API keys).
- Embeddings service must be available for title embedding generation.
- `MIGRATION_ENABLED=true` on the worker to register summary consumer tasks.

### Quick Start (Docker Compose)

```bash
make up-migration
```

This starts both the chunk migration and the summary backfill producers. The summary producer will wait for documents to be available in the source DB.

To run the summary backfill independently (after chunk migration is done):

```bash
# Start just the summary worker + summary producer
MIGRATION_ENABLED=true docker compose -f docker-compose.dev.yml up -d aviator-summary-worker summary-producer
```

### Quick Start (Local Development)

```bash
# Start the summary worker (listens on summary queues)
MIGRATION_ENABLED=true celery -A aviator.celery worker -l info -P prefork \
  --queues=aviator-summaries,csai-adt-summary

# Run the summary producer (in another terminal)
MIGRATION_SOURCE_DSN=postgresql://... uv run python -m migration.run_summary_producer
```

### Configuration

Summary-specific migration variables (all use `MIGRATION_` prefix):

| Variable                        | Default              | Description                                            |
|---------------------------------|----------------------|--------------------------------------------------------|
| `MIGRATION_SUMMARY_BATCH_SIZE`  | `10`                 | Documents per summary batch / queue message            |
| `MIGRATION_SUMMARY_QUEUE`       | `csai-adt-summary`   | Queue / topic name for summary batches                 |
| `MIGRATION_SOURCE_DSN`          | —                    | Source PostgreSQL connection string (CSAI) **required** |
| `MIGRATION_SOURCE_TABLE`        | `testlangchain`      | Source table name                                      |
| `MIGRATION_SOURCE_SCHEMA`       | `public`             | Source PostgreSQL schema                               |
| `MIGRATION_DRY_RUN`             | `false`              | Log what would be done without publishing              |

### Docker Compose Services

The `docker-compose.dev.yml` includes two summary-specific services:

- **`aviator-summary-worker`** — Dedicated Celery worker that listens on `aviator-summaries,csai-adt-summary` queues. Always runs (not behind a profile).
- **`summary-producer`** — Runs `python -m migration.run_summary_producer`. Only starts with `profiles: [migration]`.

### Checkpoint

Summary backfill uses the same `migration_state` table but with a separate checkpoint ID:

| Field       | Value                                           |
|-------------|--------------------------------------------------|
| `id`        | `summary_backfill`                               |
| `last_id`   | Last processed `documentID` (cursor-based pagination) |
| `status`    | `in_progress` or `completed`                     |
| `total_rows`| Total distinct documents in source               |
| `migrated`  | Documents scanned so far                         |

If interrupted, the producer resumes from the last checkpoint — no work is repeated.

### Error Handling

- **Per-document isolation**: If summary generation fails for one document (LLM error, embedding failure), the batch continues processing remaining documents.
- **Title embedding failure**: Non-fatal — the summary is still stored without title embeddings.
- **Retry with backoff**: Consumer tasks retry up to 7 times with exponential backoff on any exception.
- **Producer resilience**: Source DB read failures retry up to 5 times with exponential backoff (5s, 10s, 20s, 40s, 80s) before failing.
- **Container restart**: Producer container runs with `restart: on-failure` and applies a 30-second backoff before exiting on unrecoverable errors.

### Map-Reduce Summarization

Documents with content exceeding the configured token threshold (`summary_content_threshold_tokens`) are automatically split into batches and summarized using a Map-Reduce approach:

1. Content is split using `RecursiveCharacterTextSplitter` (chunk size = threshold × 4 characters).
2. Each batch is summarized individually (map phase).
3. Batch summaries are joined with `\n\n` and summarized again (reduce phase).

The threshold is computed as `model_context_window × summary_model_input_token_limit_threshold` (default 70% of the provider's context window).

### Kubernetes Deployment

Summary backfill runs as a separate Helm-managed Job, configured via `summaryMigration.*` values:

```yaml
summaryMigration:
  enabled: true
  waitForMigration: true  # Wait for chunk migration to complete before starting
  config:
    MIGRATION_SOURCE_HOST: "source-db-host"
    MIGRATION_SOURCE_USER: "postgres"
    MIGRATION_SOURCE_DATABASE: "CSAI"
    MIGRATION_SOURCE_PASSWORD_KEY: "projects/<project>/secrets/source-db-password/versions/latest"
```

When `waitForMigration: true` (default), an init container polls the `migration_state` table until the chunk migration (`id=csai_to_adt`) reaches `status=completed` before starting the summary producer. Set `activeDeadlineSeconds` to prevent indefinite waiting.

| Helm Value                                | Default     | Description                                    |
|-------------------------------------------|-------------|------------------------------------------------|
| `summaryMigration.enabled`                | `false`     | Create the summary backfill Job                |
| `summaryMigration.waitForMigration`       | `true`      | Wait for chunk migration completion            |
| `summaryMigration.backoffLimit`            | `3`         | Job retries before failure                     |
| `summaryMigration.ttlSecondsAfterFinished`| `86400`     | Auto-delete Job after 24h                      |
| `summaryMigration.activeDeadlineSeconds`  | —           | Max runtime (recommended with waitForMigration)|
| `summaryMigration.config.*`               | —           | Non-sensitive env vars (source DB, batch size)  |
| `summaryMigration.secrets.items.*`        | —           | Sensitive values (source DSN or password)       |

### Recommended Deployment Sequence

1. Deploy worker with `MIGRATION_ENABLED=true`
2. Run chunk migration: `migration.enabled=true`
3. Wait for chunk migration to complete
4. Run summary backfill: `summaryMigration.enabled=true`
5. Verify summaries: check `workspace_document_summaries` table row counts per schema

---

## Concurrency Notes

- **Producer**: Single-threaded. Reads in order, publishes batches. Only one producer should run at a time (enforced by checkpoint row lock: `SELECT ... FOR UPDATE`).
- **Consumer**: Multiple Celery workers can run concurrently (the aviator worker uses `--autoscale`). Each batch is independent. `ON CONFLICT DO NOTHING` ensures idempotency even if the same batch is retried.
- **Schema creation**: Uses `CREATE SCHEMA IF NOT EXISTS` + `PGEngine` table init with thread-safe double-checked locking to handle concurrent first-access from multiple workers.
