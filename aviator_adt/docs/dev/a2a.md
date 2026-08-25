# Agent-to-Agent (A2A) Implementation

Content Aviator implements the [A2A Protocol v1.0](https://google.github.io/A2A) — a standard for agent-to-agent communication over HTTP using JSON-RPC 2.0.

---

## API Endpoints

All endpoints are mounted under the `/agent` prefix. Rate limited at **15 requests/minute**.

| Method | URL | Auth Required | Description |
|---|---|---|---|
| `GET` | `/agent/.well-known/agent.json` | ✅ | A2A Agent Card (discovery) |
| `POST` | `/agent` | ✅ | Unified A2A endpoint — JSON-RPC 2.0 and HTTP+JSON |

An optional `A2A-Version` request header is accepted (default: `1.0`).

---

## Supported Methods

| A2A Method | Handler | Returns |
|---|---|---|
| `message/send` | `A2AChatHandler.handle_message_send` | `Task` (state: submitted) — poll with `tasks/get` |
| `message/stream` | `A2AChatHandler.handle_message_stream` | SSE stream (`text/event-stream`) |
| `tasks/get` | `TasksHandler.handle_get` | Current `Task` state + artifacts |
| `tasks/cancel` | `TasksHandler.handle_cancel` | Updated `Task` (state: canceled) |

---

## Two Wire Formats

### 1. JSON-RPC 2.0 (standard — recommended)

Wrap every request in a JSON-RPC envelope:

```json
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "method": "<method-name>",
  "params": { ... }
}
```

- `method` is **required**
- `id` is optional but recommended (echoed back in the response)
- Response is always wrapped: `{ "jsonrpc": "2.0", "id": "req-1", "result": { ... } }`

### 2. HTTP+JSON (convenience shortcut)

Send the params directly as the request body (no envelope). The method is inferred from the `Accept` header:

- `Accept: text/event-stream` → `message/stream` (SSE)
- anything else → `message/send` (JSON)

---

## Request Params Format

Use `params.message` — a single `Message` object with typed `parts` (A2A v1.0):

```json
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        { "text": "Summarise workspace 67890" }
      ],
      "contextId": "thread-uuid-for-multi-turn",
      "referenceTaskIds": ["prev-task-uuid"],
      "metadata": {
        "where": [{ "workspaceID": "67890" }],
        "inlinecitation": true
      }
    }
  }
}
```

| Field | Required | Description |
|---|---|---|
| `message.role` | ✅ | `"user"` or `"agent"` |
| `message.parts` | ✅ | Array of part objects (see [Part Types](#part-types) below) |
| `message.contextId` | ❌ | Thread ID for multi-turn conversations (maps to LangGraph `thread_id`). **Auto-generated UUID if omitted** — every task always has a stable thread identity. |
| `message.referenceTaskIds` | ❌ | IDs of prior tasks this message depends on (task chaining). Carried through to the internal context. |
| `message.metadata.where` | ❌ | Grounding — restrict to specific workspaces/documents |
| `message.metadata.inlinecitation` | ❌ | Enable inline citations (default: `true`) |

### Part Types

Messages and artifacts use typed part objects. Three part variants are supported:

| Part | Fields | Description |
|---|---|---|
| **TextPart** | `text`, `metadata?` | Plain-text content |
| **DataPart** | `data`, `mediaType?` (default `application/json`), `metadata?` | Structured JSON data |
| **FilePart** | `filename`, `mediaType`, `url?`, `raw?` (base64), `metadata?` | File reference or inline bytes |

Currently, only `TextPart` values are extracted for the LLM prompt. Non-text parts (`DataPart`, `FilePart`) are logged and silently ignored during input normalisation.

### Input Normalisation (`normalize_input`)

The `A2AAgentInvokeRequest` model validator converts the A2A v1.0 message format into the internal representation before reaching any handler:

- Joins all `TextPart.text` values into a single content string
- Maps `role: "user"` → `author: "user"`, `role: "agent"` → `author: "assistant"`
- Extracts `contextId` → `context.thread_id` (generates a UUID if absent)
- Carries `referenceTaskIds` into `context.reference_task_ids`
- Extracts `metadata.where` → top-level `where`

---

## Message Role Field

All messages must use `role: "user"` or `role: "agent"` as per A2A v1.0.

---

## Authentication

All endpoints require authentication via `require_authentication()`. Two schemes are supported:

| Scheme | Header | Value |
|---|---|---|
| OTCS Ticket | `otcsticket` | `<otcs-ticket>` |
| Bearer Token | `Authorization` | `Bearer <jwt>` |

The Bearer Auth security scheme is only advertised in the Agent Card when `OTDS_URL` is configured.

---

## Detailed Usage

### `message/send` — Fire and Poll

Creates a task, starts the LangGraph agent in the background, and returns immediately. Poll `tasks/get` for the result.

**JSON-RPC 2.0:**

```json
POST /agent
Content-Type: application/json
otcsticket: <ticket>

{
  "jsonrpc": "2.0",
  "id": "req-1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        { "text": "What is Content Aviator?" }
      ],
      "metadata": {
        "where": [{ "workspaceID": "67890" }],
        "inlinecitation": true
      }
    }
  }
}
```

**HTTP+JSON (no envelope):**

```json
POST /agent
Content-Type: application/json
Accept: application/json
otcsticket: <ticket>

{
  "message": {
    "role": "user",
    "parts": [
      { "text": "What is Content Aviator?" }
    ],
    "metadata": {
      "where": [{ "workspaceID": "67890" }],
      "inlinecitation": true
    }
  }
}
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "result": {
    "kind": "task",
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "contextId": "auto-generated-uuid",
    "status": {
      "state": "submitted",
      "timestamp": "2026-04-05T10:00:00.000000+00:00"
    },
    "artifacts": [],
    "history": []
  }
}
```

---

### `tasks/get` — Poll for Result

Fetch the current state of a task by ID. Use this after `message/send` to check if the agent has finished.

**JSON-RPC 2.0:**

```json
POST /agent
Content-Type: application/json
otcsticket: <ticket>

{
  "jsonrpc": "2.0",
  "id": "req-2",
  "method": "tasks/get",
  "params": {
    "id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**Response (still running):**

```json
{
  "jsonrpc": "2.0",
  "id": "req-2",
  "result": {
    "kind": "task",
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": { "state": "working", "timestamp": "..." },
    "artifacts": []
  }
}
```

**Response (completed):**

```json
{
  "jsonrpc": "2.0",
  "id": "req-2",
  "result": {
    "kind": "task",
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": { "state": "completed", "timestamp": "..." },
    "artifacts": [
      {
        "name": "response",
        "parts": [
          { "text": "Content Aviator is an enterprise AI system..." }
        ]
      }
    ]
  }
}
```

**Response (input required — LangGraph interrupt):**

```json
{
  "jsonrpc": "2.0",
  "id": "req-2",
  "result": {
    "kind": "task",
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": { "state": "input-required", "timestamp": "..." },
    "artifacts": [
      {
        "name": "response",
        "parts": [
          { "text": "I need more information to proceed..." }
        ]
      }
    ]
  }
}
```

---

### `message/stream` — Real-Time SSE Streaming

Returns a `text/event-stream` (SSE) response. The agent streams tokens in real time via the internal `/v1/chat/stream` WebSocket — no polling needed.

**JSON-RPC 2.0:**

```json
POST /agent
Content-Type: application/json
Accept: text/event-stream
otcsticket: <ticket>

{
  "jsonrpc": "2.0",
  "id": "req-1",
  "method": "message/stream",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        { "text": "What is Content Aviator?" }
      ],
      "metadata": {
        "where": [{ "workspaceID": "67890" }],
        "inlinecitation": true
      }
    }
  }
}
```

**HTTP+JSON (no envelope):**

```json
POST /agent
Content-Type: application/json
Accept: text/event-stream
otcsticket: <ticket>

{
  "message": {
    "role": "user",
    "parts": [
      { "text": "What is Content Aviator?" }
    ],
    "metadata": {
      "where": [{ "workspaceID": "67890" }],
      "inlinecitation": true
    }
  }
}
```

**SSE Stream (emitted in order):**

```
event: task
data: {"id":"550e8400-...","contextId":null,"status":{"state":"submitted","timestamp":"..."},"artifacts":[],"history":[]}

event: status-update
data: {"statusUpdate":{"taskId":"550e8400-...","contextId":null,"status":{"state":"working","timestamp":"..."},"final":false}}

event: status-update
data: {"statusUpdate":{"taskId":"550e8400-...","contextId":null,"status":{"state":"working","timestamp":"..."},"final":false,"progressLabel":"Thinking ...\n"}}

event: artifact-update
data: {"artifactUpdate":{"taskId":"550e8400-...","contextId":null,"artifact":{"name":"response","parts":[{"text":"Content "}],"metadata":{"node":"agent","type":"content"}}}}

event: artifact-update
data: {"artifactUpdate":{"taskId":"550e8400-...","contextId":null,"artifact":{"name":"response","parts":[{"text":"Aviator "}],"metadata":{"node":"agent","type":"content"}}}}

... (one event per token)

event: artifact-update
data: {"artifactUpdate":{"taskId":"550e8400-...","contextId":null,"artifact":{"name":"response","parts":[{"text":"Content Aviator is an enterprise AI system..."}],"metadata":{"node":"agent","type":"ai"}}}}

event: status-update
data: {"statusUpdate":{"taskId":"550e8400-...","contextId":null,"status":{"state":"completed","timestamp":"..."},"final":true}}
```

**SSE Response Headers:**

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

#### SSE Event Types

| Event | When | Notes |
|---|---|---|
| `task` | First — always | Initial `Task` object with `state: submitted` |
| `status-update` | State transitions | `final: false` until last event |
| `status-update` + `progressLabel` | Custom agent progress messages | e.g. `"Thinking ..."`, `"Executing RAG query..."` — sourced from `label`, `message`, or routing info frames |
| `artifact-update` | Each token/chunk | One per token from `/v1/chat/stream`; includes `metadata.node` (graph node name) and `metadata.type` (event type) |
| `artifact-update` | Final full answer | `type: "ai"` or `type: "done"` frame — complete assembled response (persisted to DB) |
| `status-update` | Last — always | `state: completed` (or `failed`), `final: true` |

**Frame mapping from `/v1/chat/stream`:**

- Native events use `msg.type` (`"content"`, `"ai"`, `"feedback"`, `"final"`, `"metadata"`)
- Plugin custom events use `msg.event`
- Content extracted from `msg.content` → `msg.text` → `msg.answer` (first non-empty wins)
- Frames with `type` in `{"feedback", "final", "metadata"}` are skipped (housekeeping)
- Frames with no content but a `label` / `message` / `routing_info` field are emitted as progress status-updates

**Error handling:** If the WebSocket connection fails, an SSE `status-update` with `state: failed` and `final: true` is emitted before the stream closes. The failed state is also persisted to the database.

---

### `tasks/cancel` — Cancel a Running Task

**JSON-RPC 2.0:**

```json
POST /agent
Content-Type: application/json
otcsticket: <ticket>

{
  "jsonrpc": "2.0",
  "id": "req-3",
  "method": "tasks/cancel",
  "params": {
    "id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "id": "req-3",
  "result": {
    "kind": "task",
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": { "state": "canceled", "timestamp": "..." },
    "artifacts": []
  }
}
```

Only tasks in `submitted` or `working` state can be cancelled. Cancelling a terminal task returns a `TaskNotCancelableError` (`-32002`).

**Two-layer cancellation:** The handler first requests asyncio cancellation of the in-memory background task (best-effort), then persists the `canceled` state in the database. The DB update always succeeds even if the asyncio task already completed.

---

### Agent Card — Discovery

```
GET /agent/.well-known/agent.json
otcsticket: <ticket>
```

**Response:**

```json
{
  "protocolVersions": ["1.0"],
  "name": "Content Aviator Agent",
  "version": "x.y.z",
  "description": "Enterprise AI-powered document analysis and retrieval-augmented generation agent.",
  "supportedInterfaces": [
    { "url": "https://your-host/agent", "protocolBinding": "JSON-RPC", "protocolVersion": "1.0" },
    { "url": "https://your-host/agent", "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0" }
  ],
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": false
  },
  "securitySchemes": {
    "otcsTicket": { "type": "apiKey", "name": "otcsticket", "in": "header", "description": "OpenText Content Server ticket..." },
    "bearerAuth":  { "type": "http", "scheme": "bearer", "bearerFormat": "JWT", "description": "Bearer token issued by OTDS..." }
  },
  "security": [{ "otcsTicket": [] }, { "bearerAuth": [] }],
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {
      "id": "rag_query",
      "name": "Rag Query",
      "description": "Retrieval-augmented generation query tool.",
      "inputModes": ["text/plain"],
      "outputModes": ["text/plain"],
      "examples": [],
      "tags": ["tool"]
    }
  ]
}
```

**Skills** are auto-derived from the agent's registered LangGraph tools via `SkillBuilder`. Tool names are normalised to URL-safe IDs (`to_skill_id`) and title-cased names (`to_skill_name`). Duplicate tool names (same normalised ID) are deduplicated.

**`bearerAuth`** is only included in `securitySchemes` and `security` when `OTDS_URL` is configured.

---

## Task Lifecycle

```
submitted       — task row created in DB, returned to client immediately
    │
    ▼
working         — background asyncio task started, LangGraph agent running
    │
    ├──► completed       — agent finished successfully (artifacts populated)
    ├──► input-required  — LangGraph hit an interrupt (human-in-the-loop)
    ├──► canceled        — client called tasks/cancel
    └──► failed          — unhandled exception in the agent
```

All task states defined in the `TaskState` enum:

| State | Value | Description |
|---|---|---|
| `SUBMITTED` | `submitted` | Received, not yet started (initial state) |
| `WORKING` | `working` | LangGraph graph is executing |
| `INPUT_REQUIRED` | `input-required` | LangGraph interrupt — awaiting user input |
| `COMPLETED` | `completed` | Finished successfully |
| `CANCELED` | `canceled` | Cancelled by client |
| `FAILED` | `failed` | Unrecoverable error |
| `REJECTED` | `rejected` | Agent refused the request |
| `AUTH_REQUIRED` | `auth-required` | Agent needs additional authentication |
| `UNKNOWN` | `unknown` | State cannot be determined |

> **Note:** `rejected`, `auth-required`, and `unknown` are defined for spec compliance but not currently used in the standard flow.

### Interrupt Handling (Human-in-the-Loop)

When the LangGraph agent returns with `context.interrupt = true`, the task runner transitions the task to `input-required` instead of `completed`. The client must poll `tasks/get` to detect this state and send a follow-up `message/send` with the same `contextId` to resume the conversation.

---

## JSON-RPC Error Codes

| Code | Constant | Meaning |
|---|---|---|
| `-32600` | `JSONRPC_INVALID_REQUEST` | Malformed JSON-RPC envelope |
| `-32601` | `JSONRPC_METHOD_NOT_FOUND` | Method name not registered |
| `-32602` | `JSONRPC_INVALID_PARAMS` | Parameter validation failed |
| `-32603` | `JSONRPC_INTERNAL_ERROR` | Unhandled server error |
| `-32000` | `JSONRPC_SERVER_ERROR` | Implementation-defined server error (range `-32000` to `-32099`) |
| `-32001` | `JSONRPC_TASK_NOT_FOUND` | No task with the given ID |
| `-32002` | `JSONRPC_TASK_NOT_CANCELABLE` | Task already in terminal state |
| `-32004` | `JSONRPC_UNSUPPORTED_OPERATION` | Operation not supported by this agent |
| `-32005` | `JSONRPC_CONTENT_TYPE_NOT_SUPPORTED` | Input content type not accepted |

**Error response shape:**

```json
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "error": {
    "code": -32001,
    "message": "Task '550e8400-...' not found"
  }
}
```

**Exception → error code mapping in `router.py`:**

| Exception | Code | HTTP Status |
|---|---|---|
| `TaskNotFoundError` | `-32001` | 404 |
| `TaskNotCancelableError` | `-32002` | 409 |
| Generic `Exception` | `-32603` | 500 |

---

## Database Layer

### `A2ATask` ORM Model

Each `message/send` or `message/stream` invocation creates a row in the `a2a_tasks` table:

| Column | Type | Description |
|---|---|---|
| `id` | `String` (PK) | UUID task identifier |
| `context_id` | `String` (nullable) | LangGraph `thread_id` for multi-turn conversations |
| `state` | `String` | Current `TaskState` value (default: `"submitted"`) |
| `artifacts` | `JSON` (nullable) | Result payload: `{"result": "...", "references": [...], "context": "...", "where": [...]}` |
| `error` | `Text` (nullable) | Error message when `state = "failed"` |
| `created_at` | `DateTime` | Row creation timestamp (UTC) |
| `updated_at` | `DateTime` | Last modification timestamp (UTC, auto-updated) |

### Multi-Tenant Isolation

Tasks are tenant-isolated via PostgreSQL `schema_translate_map`. The tenant schema is resolved from `user.tenantId` using `tenant_id_to_schema_name()`. All DB operations are routed through the correct schema.

### Repository & Service Pattern

- **`A2ATaskRepository`** — Low-level CRUD operations. Receives an already-scoped SQLAlchemy `Session`.
  - `create(task_id, context_id)` — Insert row in `submitted` state
  - `get(task_id)` — Fetch by ID
  - `update_state(task_id, state, artifacts?, error?)` — Transition state
  - `cancel(task_id)` — Set to `canceled` only if still in `submitted` or `working`
- **`A2ATaskService`** (singleton: `task_service`) — Entry point for all A2A DB operations. Manages session lifecycle via `database_manager.session()`. No session objects are ever exposed to callers.

---

## Configuration

| Setting | Env Variable | Description |
|---|---|---|
| `aviator_chat_service_endpoint` | `AVIATOR_CHAT_SERVICE_ENDPOINT` | Cluster-internal URL for A2A → `/v1/chat` loopback calls. Keeps traffic within the service mesh, bypasses ingress. |
| `root_path` | `ROOT_PATH` | URL prefix appended to base URLs for both internal loopback and external agent card URLs. |
| `otds_url` | `OTDS_URL` | When set, Bearer Auth is advertised in the Agent Card `securitySchemes`. |

---

### Request Flow

```
POST /agent  (router.py)
     │
     ├─ JSON-RPC path ──► JsonRpcRequest.model_validate()
     │                         │
     │                         └── method_registry.get(method)  ◄── registry.py
     │
     └─ HTTP+JSON path ──► A2AAgentInvokeRequest.model_validate()
                                │
                                └── method_registry.get(inferred_method)

method_registry (registry.py)
  ├── "message/send"    → A2AChatHandler.handle_message_send()
  │       ├── task_service.create()    ──► DB row (state: submitted)
  │       └── task_runner.start_task() ──► _run() in background
  │                                             ├── task_service.update_state() → working
  │                                             ├── A2AChatHandler.process()
  │                                             │       └── POST /v1/chat (internal HTTP via AVIATOR_CHAT_SERVICE_ENDPOINT)
  │                                             └── task_service.update_state() → completed | input-required | failed
  │
  ├── "message/stream"  → A2AChatHandler.handle_message_stream()
  │       ├── task_service.create()      ──► DB row (state: submitted)
  │       └── ws_to_a2a_sse_stream()     ──► connects to /v1/chat/stream WebSocket
  │               ├── task_service.update_state() → working
  │               ├── relays tokens as SSE artifact-update events
  │               └── task_service.update_state() → completed | failed
  │
  ├── "tasks/get"       → TasksHandler.handle_get()
  │       └── task_service.get(task_id)
  │
  └── "tasks/cancel"    → TasksHandler.handle_cancel()
          ├── task_runner.cancel_task()      (best-effort asyncio cancellation)
          └── task_service.cancel(task_id)   (DB state → canceled)
```

### Internal Loopback

`A2AChatHandler` communicates with the chat endpoints via internal HTTP/WebSocket calls:

- **`message/send`** → `POST {AVIATOR_CHAT_SERVICE_ENDPOINT}/v1/chat` via `httpx.AsyncClient` (60s timeout)
- **`message/stream`** → `ws(s)://{AVIATOR_CHAT_SERVICE_ENDPOINT}/v1/chat/stream` via `websockets` library (10s open timeout)
- Auth headers (`authorization`, `otcsticket`) and cookies are forwarded from the original A2A request

This design keeps A2A traffic within the service mesh and avoids round-trips through the ingress/gateway.

---

## Extending with New Methods

Register a new method without touching `router.py`:

```python
from aviator.a2a.handler import method_registry

async def my_handler(payload: dict, request: Request, user: dict):
    # ... your logic
    return Task(...)

method_registry.register("my/new-method", my_handler)
```

Call this from a plugin's `startup_extension` or directly in `registry.py`.

---

## Exception Hierarchy

A2A-specific exceptions are defined in `aviator.exceptions`:

```python
class A2ATaskError(Exception):
    """Base exception for A2A task-related errors."""

class TaskNotFoundError(A2ATaskError):
    """Raised when task ID cannot be found."""

class TaskNotCancelableError(A2ATaskError):
    """Raised when task is in terminal state."""
```
