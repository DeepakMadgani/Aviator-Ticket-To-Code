# Kubernetes Deployments

## Docker Desktop (Local Development)

Deploy Aviator ADT to a local Docker Desktop Kubernetes cluster using pre-built images from the registry — no local build required.

### Prerequisites

- **Docker Desktop** with Kubernetes enabled (Settings → Kubernetes → Enable Kubernetes)
- **kubectl** installed and configured
- **skaffold** installed ([install guide](https://skaffold.dev/docs/install/))
- **helm** v3 installed
- **Google Service Account credentials**: `otl-cs-csai.json` in the project root (obtain from your team or GCP project)

### Step-by-Step Deployment

#### 1. Verify kubectl context

Ensure your kubectl context is set to `docker-desktop`:

```bash
kubectl config use-context docker-desktop
```

Verify:

```bash
kubectl config current-context
# Should output: docker-desktop
```

#### 2. Create a namespace (optional but recommended)

```bash
kubectl create namespace aviator
```

If using a custom namespace, create a `skaffold.env` file in the project root:

```bash
echo "SKAFFOLD_NAMESPACE=aviator" > skaffold.env
```

> If you skip this, resources are deployed to the `default` namespace.

#### 3. Create the GSA credentials secret

The Aviator containers need Google Service Account credentials mounted as a Kubernetes secret. Create it from your local credentials file:

```bash
# For default namespace:
kubectl create secret generic aviator-files --from-file=gsa=./otl-cs-csai.json

# For a custom namespace:
kubectl create secret generic aviator-files --from-file=gsa=./otl-cs-csai.json -n aviator
```

#### 4. Build the plugin image (if using plugins)

If the `docker-desktop.yaml` values file includes init containers (e.g., `aviator-plugin-sample`), build the plugin image locally so Kubernetes can use it without pulling from a remote registry:

```bash
# From the aviator_adt project root, build the plugin init container from the sibling repo
# IMPORTANT: Use --target final to build the lightweight init container image (busybox-based),
# NOT the full runtime image which uses the ADT entrypoint and will fail as an init container.
docker build --target final -t artifactory.otxlab.net/cs-csai-docker-dev/aviator-plugin-sample:latest ../aviator-plugin-sample
```

The `docker-desktop.yaml` uses `pullPolicy: IfNotPresent` for plugin images, so a locally built image is used if available.

> **Tip:** To skip plugins, remove or comment out the `initContainers.containers` section in `helm/site-specific-values/docker-desktop.yaml`.

#### 5. Deploy backend services (PostgreSQL + RabbitMQ)

```bash
skaffold -f ./skaffold-backend.yaml run -p docker-desktop
```

Wait for pods to be ready:

```bash
kubectl get pods --watch
# Or for a custom namespace:
kubectl get pods -n aviator --watch
```

#### 6. Deploy Aviator ADT

Using pre-built images (no local build needed):

```bash
NO_BUILD=1 skaffold run -p docker-desktop
```

Or, to build locally from source and deploy:

```bash
skaffold run -p docker-desktop
```

#### 7. Verify the deployment

```bash
kubectl get pods
# Or for a custom namespace:
kubectl get pods -n aviator
```

Expected pods:

| Pod | Description |
|-----|-------------|
| `aviator-*` | Main FastAPI API server |
| `aviator-worker-*` | Celery worker for embeddings |
| `aviator-summary-worker-*` | Celery worker for document summaries (disabled by default in docker-desktop) |
| `aviator-beat-*` | Celery Beat scheduler |
| `aviator-backend-pgvector-*` | PostgreSQL with pgvector |
| `aviator-backend-rabbitmq-*` | RabbitMQ message broker |

Test the API:

```bash
# Port-forward the service
#kubectl port-forward svc/chat-svc 3000:80
kubectl port-forward deployment/aviator 3000:3000 -n aviator

# Health check
curl http://localhost:3000/health

# Chat endpoint
curl -X POST http://localhost:3000/v1/chat \
  -H "Content-Type: application/json" \
  -H "auth-ticket: test" \
  -d '{"messages": [{"author": "user", "content": "Hello"}]}'
```

### Tear Down

```bash
# Remove Aviator
NO_BUILD=1 skaffold delete -p docker-desktop

# Remove backend services
skaffold -f ./skaffold-backend.yaml delete -p docker-desktop

# Remove the secret
kubectl delete secret aviator-files -n aviator

# Remove namespace (if created)
kubectl delete namespace aviator
```

### Troubleshooting

| Issue | Solution |
|-------|----------|
| Pod stuck in `CreateContainerConfigError` | The `aviator-files` secret is missing. Run step 3 above. |
| Init container `ImagePullBackOff` | Plugin image not built locally. Run step 4, or set `pullPolicy: Never`. |
| `ErrImagePull` for aviator image | Ensure Docker is logged into the registry: `docker login artifactory.otxlab.net` |
| Backend pods not ready | Wait longer or check logs: `kubectl logs <pod-name>` |

---

## GKE otl-cs-csai Environment

To deploy to the `gke_otl-cs-csai_us-east4_otl-cs-csai-cluster1` GKE cluster:

1) Ensure the current kubectl context is equal to `gke_otl-cs-csai_us-east4_otl-cs-csai-cluster1`:

    ```bash
    kubectl config use-context gke_otl-cs-csai_us-east4_otl-cs-csai-cluster1 && kubectl config current-context
    ```

2) Ensure you have a `skaffold.env` created in the project root with your target namespace:

    ```bash
    SKAFFOLD_NAMESPACE=your-namespace
    ```

3) Create the GSA credentials secret:

    ```bash
    kubectl create secret generic aviator-files --from-file=gsa=./otl-cs-csai.json -n your-namespace
    ```

4) Bring up the backend services:

    ```bash
    skaffold run -f ./skaffold-backend.yaml -n your-namespace
    ```

5) Bring up Content Aviator:

    ```bash
    skaffold run -n your-namespace
    ```

## Celery Beat Scheduler

The Helm chart includes an optional **Celery Beat** deployment that runs periodic scheduled tasks. This is required when usage tracking is enabled to ensure old transaction and tally rows are cleaned up automatically.

### What It Runs

- **`cleanup_usage_transactions`** — Runs daily at 02:00 UTC; batch-deletes `usage_transactions` rows older than `USAGE_TRACKING_RETENTION_DAYS` and `usage_daily_tallies` rows older than `USAGE_TRACKING_TALLY_RETENTION_DAYS`.
- **`cleanup_checkpoints`** — Runs daily at 03:00 UTC; deletes LangGraph checkpoint rows (checkpoints, blobs, writes) for threads idle longer than `CHECKPOINTER_RETENTION_HOURS`.
- Any additional schedules contributed by plugins via the `aviator.celery_beat_schedules` entry point.

### Enabling Beat

Set `beat.enabled=true` in your Helm values:

```yaml
beat:
  enabled: true
```

This deploys a single-replica Beat pod (`celery -A aviator.celery beat`) alongside the API server and worker. Only **one Beat replica** should run at a time to avoid duplicate task execution.

### Local Development

When running locally (outside Kubernetes), start the worker with the `-B` flag to embed the Beat scheduler:

```bash
uv run celery -A aviator.celery worker -B -l info -P solo
```

Or run Beat as a separate process:

```bash
uv run celery -A aviator.celery beat -l info
```


## Plugin Deployment with Init Containers

For guidance on deploying plugins using Kubernetes init containers, see [initContainers.md](./initContainers.md).  
This document explains how to use init containers to copy plugin files or perform setup tasks before the main application starts.

## CSAI → ADT Migration

The Helm chart supports running the CSAI → ADT vector store migration as a Kubernetes **Job**. When enabled, the chart creates:

1. A **Job** (`aviator-migration`) that runs the migration producer to completion.
2. The **worker** automatically subscribes to the migration queue (`csai-adt-migration`) alongside its normal embedding queue.

When migration is disabled (default), no migration-related resources are created and the worker behaves normally.

For full details on the migration process, see [migration.md](./migration.md).

### Enabling Migration

Set `migration.enabled=true` in your Helm values (via `--set` or a values file):

```bash
# Deploy with migration enabled
skaffold run -p docker-desktop --set migration.enabled=true \
  --set 'migration.secrets.items.MIGRATION_SOURCE_DSN=postgresql://user:pass@source-host:5432/CSAI'
```

Or create a values override file (e.g., `helm/site-specific-values/migration.yaml`):

```yaml
migration:
  enabled: true
  secrets:
    items:
      MIGRATION_SOURCE_DSN: "postgresql://user:pass@source-host:5432/CSAI"
  config:
    MIGRATION_BATCH_SIZE: "500"
    # MIGRATION_DRY_RUN: "true"
    # MIGRATION_DEFER_INDEXES: "true"
    # MIGRATION_LOG_LEVEL: "INFO"
```

### What Happens When Enabled

- **ConfigMap**: `MIGRATION_ENABLED=true` is injected into the shared ConfigMap along with migration config (source DB settings), so the worker registers migration consumer tasks and can connect to the source DB.
- **Worker**: `WORKER_QUEUES` is set to `aviator-embeddings,csai-adt-migration`, enabling the worker to process both embedding and migration tasks. Workers also get access to migration secrets.
- **Job**: A Kubernetes Job runs the migration producer (`python -m migration.run_producer`), which counts rows in the source DB and publishes `(offset, limit)` batches to the migration queue. The Job completes once all batches are published.
- **Workers fetch rows**: Each worker receives an `(offset, limit)` batch, connects to the source DB, fetches rows using `ORDER BY id OFFSET/LIMIT`, groups by tenant schema, and writes to the target DB.
- **Secret**: Migration-sensitive values (e.g., `MIGRATION_SOURCE_DSN`) are stored in a dedicated `aviator-migration-secrets` Secret, mounted to both the Job and workers.

#### GCP Production Notes

The migration Job fully supports GCP infrastructure:

- **Cloud SQL Proxy**: When `cloudsqlproxy.enabled=true`, the Job includes a sidecar with the `--quitquitquit` flag. After the producer finishes, it signals the proxy to shut down using `cloudsqlproxy.quitquitUrl`, so the pod terminates cleanly.
- **Secret Manager**: When `SECRETS_MANAGER=google` is set in the shared ConfigMap, the producer resolves the target database password from Google Secret Manager at startup — no `POSTGRES_PASSWORD` needed.
- **Workload Identity**: When `gcp.workloadIdentity.enabled=true`, the Job uses the worker's service account for authentication.

See [migration.md — GCP Production Deployment](./migration.md#gcp-production-deployment) for example values.

### Configuration

Migration-specific settings are split into two categories:

| Category | Helm Path | Description |
|----------|-----------|-------------|
| Non-sensitive | `migration.config.*` | Set as env vars on the Job (e.g., `MIGRATION_BATCH_SIZE`) |
| Sensitive | `migration.secrets.items.*` | Stored in a K8s Secret (e.g., `MIGRATION_SOURCE_DSN`) |

Target database, broker, and vector store settings come from the shared ConfigMap (same as the app and worker).

### Job Lifecycle

| Setting | Default | Description |
|---------|---------|-------------|
| `migration.backoffLimit` | `3` | Retries before the Job is marked failed |
| `migration.ttlSecondsAfterFinished` | `86400` | Auto-cleanup after 24 hours |
| `migration.activeDeadlineSeconds` | — | Max runtime (no limit by default) |

The producer uses checkpoint-based resumption, so retries resume from the last committed batch rather than starting over.

### Monitoring

```bash
# Watch the migration Job
kubectl get jobs -l app=aviator-migration --watch

# View producer logs
kubectl logs job/aviator-migration -f

# Check Job status
kubectl describe job aviator-migration
```

### Tear Down

After migration completes, disable migration and redeploy to remove the Job and restore normal worker behaviour:

```bash
skaffold run -p docker-desktop  # migration.enabled defaults to false
```

Or delete the Job manually:

```bash
kubectl delete job aviator-migration
```


## Observability for LoB Teams (AKS)

Content Aviator ADT provides built-in observability for every AI interaction through [Langfuse](https://langfuse.com/), an open-source LLM observability platform. This enables detailed tracing and monitoring of all plugin and agent activity.

For Line-of-Business (LoB) teams deploying Aviator ADT plugins—whether running locally or in a cloud Kubernetes environment—refer to the following guide for enabling and accessing observability traces:

- [Configuration of AKS Observability service for LoB teams](https://confluence.opentext.com/pages/viewpage.action?spaceKey=CSAI&title=Configuration+of+AKS+Observability+service+for+LoB+teams){:target="_blank"}

This guide explains how to configure the AKS Observability service, access Langfuse traces, and best practices for monitoring plugin deployments in both development and production environments.