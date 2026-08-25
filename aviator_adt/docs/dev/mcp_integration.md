# MCP Integration Guide

This guide covers both sides of MCP (Model Context Protocol) in Aviator ADT:

- **MCP Client** — Aviator connects *outward* to external MCP servers (e.g. Tavily, custom tools) and exposes their tools to the LangGraph agent.
- **MCP Server** — Aviator exposes its own tools *inward* to external MCP clients (e.g. SAP, Salesforce, other agents).

---

## Part 1 — MCP Client

The MCP client connects Aviator to one or more remote MCP servers. Each server's tools are loaded per tenant and injected into the agent graph.

### 1.1 How it works

`MCPClientManager` is a singleton service that:

1. Loads server configs from the database for the calling tenant (keyed by `tenantId` in the user context).
2. Injects OTDS tokens from the incoming request into any `OTDS_TOKEN` auth server configs.
3. Connects to each active server via `MultiServerMCPClient` (from `langchain-mcp-adapters`).
4. Filters the discovered tools by `tool_scope` before handing them to the graph.

### 1.2 Configuring MCP servers — CRUD API

All MCP server configs are stored per tenant in the tenant schema. The management endpoints are under `/mcp/servers`.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/mcp-client/servers` | Create a server config |
| `GET` | `/mcp-client/servers` | List all server configs for this tenant |
| `GET` | `/mcp-client/servers/{server_id}` | Get a specific server config |
| `PUT` | `/mcp-client/servers/{server_id}` | Update a server config |
| `DELETE` | `/mcp-client/servers/{server_id}` | Delete a server config |
| `GET` | `/mcp-client/tools` | List all tools currently loaded from remote servers |
| `POST` | `/mcp-client/tools/refresh` | Force re-discovery of tools from all servers |

All endpoints require authentication. Tenant context is derived from the Bearer token.

### 1.3 MCPServerConfig — field reference

```json
{
  "name": "tavily",
  "active": true,
  "url": "https://mcp.tavily.com/mcp/",
  "transport": "streamable_http",
  "tool_scope": "default",
  "auth_schema": {
    "type": "APIKEY",
    "method": "header",
    "key_name": "Authorization",
    "apikey": "tvly-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | ✅ | Unique name for this server within the tenant. Also used as tool name prefix (e.g. `tavily_web_search`). |
| `active` | `bool` | | Whether this server is included during tool discovery. Default `true`. |
| `url` | `string` | HTTP only | Full URL of the MCP server endpoint. |
| `transport` | `string` | | Transport type. `streamable_http` (default) or `sse`. |
| `command` | `string` | STDIO only | Command to launch a local STDIO server. |
| `args` | `list[string]` | STDIO only | Arguments for the STDIO command. |
| `tool_scope` | `string` | | `"default"` or `"custom"`. Controls which graph node sees these tools. See [§1.5](#15-tool_scope--graph-node-mapping). |
| `auth_schema` | `object` | | Authentication configuration. See [§1.4](#14-auth_schema--authentication). |

### 1.4 `auth_schema` — authentication

Three authentication types are supported.

#### APIKEY

Sends a static key in a header or query parameter.

```json
{
  "type": "APIKEY",
  "method": "header",
  "key_name": "Authorization",
  "apikey": "Bearer tvly-xxxx"
}
```

```json
{
  "type": "APIKEY",
  "method": "queryparam",
  "key_name": "api_key",
  "apikey": "my-secret-key"
}
```

| Field | Required | Notes |
|---|---|---|
| `type` | ✅ | `"APIKEY"` |
| `method` | ✅ | `"header"` or `"queryparam"` |
| `key_name` | | Header or param name. Default `"Authorization"`. |
| `apikey` | ✅ | The key value. Include `Bearer ` prefix if required by the server. |

#### OAUTH

Obtains a token via OAuth2 (`client_credentials` or `password` grant) before each connection.

```json
{
  "type": "OAUTH",
  "method": "payload",
  "token_url": "https://auth.example.com/oauth/token",
  "client_id": "my-client-id",
  "client_secret": "my-client-secret",
  "scope": "openid profile",
  "grant_type": "client_credentials"
}
```

Password grant:

```json
{
  "type": "OAUTH",
  "method": "payload",
  "token_url": "https://auth.example.com/oauth/token",
  "client_id": "my-client-id",
  "client_secret": "my-client-secret",
  "grant_type": "password",
  "username": "svc-account",
  "password": "svc-password"
}
```

#### OTDS_TOKEN

Forwards the caller's OTDS Bearer token directly to the MCP server. No client credentials needed — the token is extracted from the incoming request at runtime.

```json
{
  "type": "OTDS_TOKEN",
  "method": "header",
  "key_name": "Authorization"
}
```

Use this when the MCP server is an internal Aviator service or another OpenText product that accepts the same OTDS token. The token is injected automatically from the request's `Authorization` header before every tool discovery call.

### 1.5 `tool_scope` — graph node mapping

`tool_scope` determines which part of the LangGraph agent receives the tools from a given server.

| `tool_scope` value | Which tools are included | How tools are loaded |
|---|---|---|
| `"default"` | Tools routed to the **assistant node** (the main agent loop) | `mcp_client_manager.get_assistant_tools(user, request)` |
| `"custom"` | Tools routed to **plugin-defined nodes** only | `mcp_client_manager.get_plugin_mcp_tools(user, request)` |

#### Default scope — assistant node

Tools from `"default"` servers are merged with Aviator's built-in tools (RAG, datetime, chart generator) and passed to the assistant node automatically. No plugin code required.

#### Custom scope — plugin nodes

A plugin that defines a custom node calls `get_plugin_mcp_tools()` to retrieve only `"custom"`-scoped tools:

```python
from aviator.mcp import mcp_client_manager

class MyPluginNode:
    async def run(self, state, config):
        user = config["configurable"].get("user")
        request = config["configurable"].get("request")

        # Only tools with tool_scope="custom" are returned
        tools = await mcp_client_manager.get_plugin_mcp_tools(user=user, request=request)

        # Bind to LLM and invoke
        llm_with_tools = llm.bind_tools(tools)
        response = await llm_with_tools.ainvoke(state["messages"])
        return {"messages": [response]}
```

Tool names from remote servers are prefixed with the server name: a tool named `web_search` on a server named `tavily` becomes `tavily_web_search`. This prevents collisions when multiple servers expose tools with the same name.

### 1.6 Tool discovery and caching

- Tools are discovered per tenant on the first request.
- The cache is currently **not persisted** across requests (see the `TEMP` comment in `manager.py`). Each request re-loads from the database.
- Calling `POST /mcp/tools/refresh` explicitly invalidates the cache and forces re-discovery.
- When an `OTDS_TOKEN` server config's token changes, the cache for that tenant is automatically invalidated.

---

## Part 2 — MCP Server

The Aviator MCP server exposes Aviator tools to external MCP clients over HTTP (Streamable HTTP transport). It runs as a separate process (port `8100` by default, configurable via `MCP_PORT`).

### 2.1 Architecture

```
External MCP client
       │
       │  POST /  (Streamable HTTP)
       ▼
AviatorMCPServer (FastMCP)
       │
       ├── AuthMiddleware          ← validates Bearer token or OTCS ticket
       ├── _list_tools override    ← filters by tenant_lookup_config
       └── call_tool override      ← enforces per-tenant permissions
```

Authentication uses the same `require_authentication()` contract as the REST API — the same Bearer token or `otcsticket` header works for both.

### 2.2 Exposing tools with `@mcp_expose`

Only tools explicitly decorated with `@mcp_expose` are discoverable by external MCP clients. Tools without this decorator are registered in the LangGraph agent normally but are **never exposed** via the MCP server.

```python
from langchain_core.tools import tool
from aviator.mcp import mcp_expose

# Expose with default True
@mcp_expose
@tool
async def holidays_canada(province_id: str) -> str:
    """Return public holidays for a Canadian province."""
    ...

# Explicitly hide (useful when a base module exposes by default)
@mcp_expose(False)
@tool
async def internal_debug_tool(query: str) -> str:
    """Internal tool — never expose to MCP clients."""
    ...
```

**Decorator placement** — `@mcp_expose` must be the **outermost** decorator (above `@tool`). The decorator sets `_mcp_expose = True | False` on the resulting `BaseTool` object.

#### For plugin tools

Plugin tools follow the same pattern. Register the tool via the `aviator.tool_modifiers` entry point and mark it with `@mcp_expose`:

```python
# my_plugin/tools.py
from langchain_core.tools import tool
from aviator.mcp import mcp_expose

@mcp_expose
@tool
async def my_plugin_tool(query: str) -> str:
    """My plugin tool description."""
    ...

def modify_tools(tools, state, config):
    tools.append(my_plugin_tool)
```

### 2.3 Binding tools to a tenant — lookup config API

Before any tool can be called, an administrator must register the allowed tools in `tenant_lookup_config`. Two modes are supported based on the claims present in the Bearer token:

| Token claims | Schema used | `subscription_id` stored |
|---|---|---|
| Both `tenantId` **and** `subscriptions` | `tenant_<tenantId>` | Value from token |
| **Neither** `tenantId` nor `subscriptions` | `public` | `NULL` |
| Only one of the two | — | `400 Bad Request` |

The following endpoints manage this:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/mcp-server/list/alltools` | List every tool available on the MCP server |
| `POST` | `/mcp-server/register/tools` | Register (allow) tools for this tenant/subscription |
| `GET` | `/mcp-server/list/tools` | List registered tools for this tenant/subscription |
| `DELETE` | `/mcp-server/tools` | Remove specific tools from the allowed list |

#### Step 1 — discover available tools

```http
GET /mcp-server/list/alltools
Authorization: Bearer <admin-token>
```

Response:

```json
[
  { "name": "holidays_canada", "description": "Return public holidays for a Canadian province." },
  { "name": "rag_query",       "description": "Query the vector store for relevant documents." }
]
```

#### Step 2 — register tools

The token claims determine which schema the config is written to.

**Tenant schema** (token carries both `tenantId` and `subscriptions`):

```http
POST /mcp-server/register/tools
Authorization: Bearer <tenant-admin-token>
Content-Type: application/json

{
  "tools": ["holidays_canada", "rag_query"]
}
```

Response (`201 Created`):

```json
{
  "id": 1,
  "key": "ALLOWED_MCP_TOOLS",
  "value": "[{\"name\": \"holidays_canada\", \"description\": \"...\"}, {\"name\": \"rag_query\", \"description\": \"...\"}]",
  "subscription_id": "sub-001"
}
```

**Public schema** (token carries neither `tenantId` nor `subscriptions` — single-tenant / anonymous deployments):

```http
POST /mcp-server/register/tools
Authorization: Bearer <admin-token-no-tenant>
Content-Type: application/json

{
  "tools": ["holidays_canada", "rag_query"]
}
```

Response (`201 Created`):

```json
{
  "id": 1,
  "key": "ALLOWED_MCP_TOOLS",
  "value": "[{\"name\": \"holidays_canada\", \"description\": \"...\"}, {\"name\": \"rag_query\", \"description\": \"...\"}]",
  "subscription_id": null
}
```

Only tools present in `discover_tools()` are accepted. Requesting a tool that doesn't exist returns `400 Bad Request`.

#### Step 3 — remove tools

```http
DELETE /mcp-server/tools
Authorization: Bearer <tenant-admin-token>
Content-Type: application/json

{
  "tools": ["holidays_canada"]
}
```

If all tools are removed, the entire `tenant_lookup_config` row is deleted.

#### Access control at call time

When an external MCP client calls `tools/list` or `tools/call`:

1. **AuthMiddleware** validates the Bearer token and sets the user in request context.
2. `_list_tools` reads `tenant_lookup_config` and returns only the registered subset.
3. `call_tool` re-checks the allowed list before invoking — a tool not in the allowed set returns `403 Forbidden`.

---

## Summary — which API to use

| Task | Endpoint / function |
|---|---|
| Register an external MCP server for a tenant | `POST /mcp-client/servers` |
| List external MCP tools available to the agent | `GET /mcp-client/tools` |
| Force tool re-discovery | `POST /mcp-client/tools/refresh` |
| Get tools from default-scope servers (assistant node) | `mcp_client_manager.get_assistant_tools(user, request)` |
| Get tools from custom-scope servers (plugin node) | `mcp_client_manager.get_plugin_mcp_tools(user, request)` |
| Expose a built-in or plugin tool via the MCP server | `@mcp_expose` decorator |
| List all tools available on the MCP server | `GET /mcp-server/list/alltools` |
| Allow a set of tools for a tenant on the MCP server | `POST /mcp-server/register/tools` |
| Check which tools a tenant has registered | `GET /mcp-server/list/tools` |
| Remove tools from a tenant's allowed list | `DELETE /mcp-server/tools` |
