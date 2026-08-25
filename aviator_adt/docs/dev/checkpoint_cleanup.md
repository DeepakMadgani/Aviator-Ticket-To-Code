# Checkpointer Retention & Cleanup

Aviator ADT automatically removes stale LangGraph checkpoint data based on a configurable retention window measured in hours.

This keeps checkpoint storage under control while preserving recent conversation state for active threads.

## At a Glance

| Item                   | Value                               |
| ---------------------- | ----------------------------------- |
| Cleanup schedule       | Daily at **03:00 UTC**              |
| Main retention setting | `CHECKPOINTER_RETENTION_HOURS`      |
| Default retention      | `720` hours (30 days)               |
| Allowed range          | `1` to `2160` hours (up to 90 days) |
| Requires Beat          | Yes (`beat.enabled=true`)           |

## Configuration

Set retention using the following environment variable:

| Setting                        | Env Var                        | Default | Description                                                  |
| ------------------------------ | ------------------------------ | ------- | ------------------------------------------------------------ |
| `checkpointer_retention_hours` | `CHECKPOINTER_RETENTION_HOURS` | `720`   | Hours to retain checkpoint rows. Must be between 1 and 2160. |

```bash
# Example: retain checkpoints for 48 hours only
CHECKPOINTER_RETENTION_HOURS=48
```

!!! tip "Recommended retention"
**720 hours (30 days)** is the default and a safe starting point. **2160 hours (90 days)** is the recommended upper bound for ideal retention — beyond that, the cleanup overhead and storage cost outweigh the benefit of keeping old conversation history.

!!! warning "Upper bound enforced"
Pydantic validates this setting at startup. Values outside `1–2160` will raise a `ValidationError` and prevent the application from starting.

## Celery Beat Requirement

The cleanup task is scheduled by **Celery Beat**. It runs only when the Beat scheduler is enabled in your deployment.

Use this in Helm values:

```yaml
beat:
  enabled: true
```

See [Deployments](deployments.md#celery-beat) for full Beat setup instructions.

## Observability

Each cleanup run emits clear structured logs you can monitor:

```
# When stale threads are found:
INFO  Checkpointer cleanup: 42 stale thread(s) found (cutoff=2026-02-23T03:00:00+00:00, batch_size=5000, retention=720 hours)
DEBUG Checkpointer cleanup: batch 0-42 deleted 126 rows

# When nothing needs cleaning:
DEBUG Checkpointer cleanup: no stale threads found (retention=720 hours)
```

Celery also captures the task result:

```json
{ "deleted_checkpoints": 126 }
```
