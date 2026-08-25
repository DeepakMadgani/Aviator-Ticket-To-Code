# RAG Query Filtering

The `where` clause on the chat and context API endpoints controls which documents are included in RAG (Retrieval-Augmented Generation) queries. It supports flexible filtering with OR/AND semantics, custom metadata keys, negation, and the `$contains` operator.

## Where Clause Format

The `where` field is a **list of filter objects**. The semantics are:

- **Each object** in the list is **OR'd** with the others
- **Multiple keys** within a single object are **AND'd** together

```json
{
  "where": [
    {"workspaceID": "ws1"},
    {"documentID": "doc1"}
  ]
}
```

This means: match documents in workspace `ws1` **OR** document `doc1`.

## Supported Filter Types

### Simple Equality

Filter by a single key-value pair:

```json
[{"workspaceID": "ws1"}]
```

### AND Semantics

Combine multiple conditions within a single object — all must match:

```json
[{"workspaceID": "ws1", "documentID": "doc1"}]
```

### OR Semantics

Separate objects in the list — any can match:

```json
[
  {"workspaceID": "ws1"},
  {"documentID": "doc1"}
]
```

### Custom Metadata Keys

Any metadata key can be used in filters, not just `workspaceID` and `documentID`:

```json
[
  {"docbaseName": "myDocbase"},
  {"containerID": "cont1"},
  {"tenantId": "tenantA"}
]
```

### NEQ (Not Equal)

Exclude documents matching a condition using the `_NEQ_` operator:

```json
[{"_NEQ_": {"documentID": "doc3"}}]
```

To exclude multiple values, use separate `_NEQ_` objects:

```json
[
  {"_NEQ_": {"documentID": "doc3"}},
  {"_NEQ_": {"documentID": "doc4"}}
]
```

### $contains Operator

For array-type metadata fields:

```json
[{"tags": {"$contains": "important"}}]
```

### Combined Filters

All filter types can be mixed in a single `where` clause:

```json
[
  {"workspaceID": "ws1", "docbaseName": "myDocbase"},
  {"containerID": "cont1"},
  {"_NEQ_": {"documentID": "excluded"}}
]
```

This means: ((workspaceID = ws1 **AND** docbaseName = myDocbase) **OR** (containerID = cont1)) **AND** (documentID != excluded).

!!! note
    `_NEQ_` filters are always **AND'd** globally with the rest of the conditions — they are never OR'd with positive filter groups. This ensures exclusions apply across all OR branches.

## Key Mapping

The standard API keys are mapped to internal column names:

| API Key | Internal Column |
|---------|----------------|
| `workspaceID` | `workspace_id` |
| `documentID` | `document_id` |

Custom keys (e.g. `docbaseName`, `containerID`, `tenantId`) pass through unchanged.

## Usage in API Endpoints

### POST `/v1/chat`

```json
{
  "messages": [{"author": "user", "content": "Summarize my documents"}],
  "where": [
    {"workspaceID": "ws1"},
    {"workspaceID": "ws2"},
    {"_NEQ_": {"documentID": "doc3"}}
  ]
}
```

### POST `/v1/context`

```json
{
  "query": "search term",
  "metadata": [
    {"workspaceID": "ws1"},
    {"workspaceID": "ws2"}
  ],
  "numResults": 10
}
```

!!! note
    The context endpoint uses `metadata` instead of `where`, but the filter format is identical.

## Scope Enforcement

When both **state-level** filters (from the `where` clause on the chat request) and **query-level** filters (from the LLM's RAG tool call) are present, they are merged with **AND** logic. This ensures the LLM's tool-call filters always stay within the scope defined by the original request.

## Native Columns vs. Custom Metadata Keys

langchain-postgres v2 (prior to 0.0.17) could only filter natively on real table columns. Since **langchain-postgres 0.0.17**, the library natively supports filtering on custom metadata keys stored inside the `langchain_metadata` JSONB column by translating them to PostgreSQL `->>`/`->` SQL expressions.

By default, the only declared `metadata_columns` (real table columns) are `workspace_id` and `document_id`. All other keys (e.g. `docbaseName`, `containerID`, `tenantId`) are custom metadata and are transparently handled by the library at the database level. No post-retrieval Python filtering is needed.

## Internal Architecture

### Filter Pipeline

All filtering is handled at the database level in a single tier:

```
API input (where clause)
    │
    └─► build_search_filter(where)  → MongoDB-style filter dict
            Uses: to_mongodb_filter()
            │
            └─► langchain-postgres (>=0.0.17) → SQL WHERE clause
```

`build_search_filter` converts the full where clause into a MongoDB-style filter. langchain-postgres 0.0.17+ natively translates custom metadata keys to `langchain_metadata->>'key'` SQL expressions, so the database handles all filtering.

`build_metadata_post_filter` exists for backward compatibility but always returns `None`.

### Filter Conversion Pipeline

The core conversion from API input to MongoDB-style filter follows this pipeline:

1. **API input** → `list[dict[str, Any]]` (the raw `where` clause)
3. **Group building** — Each filter object becomes an AND-group; pure `_NEQ_` entries become global AND conditions
4. **Same-key optimisation** (`_optimize_same_key_groups`) — Merges multiple single-key `$eq` / `$in` OR entries for the same field into a single `$in`
5. **`to_mongodb_filter()`** → MongoDB-style filter dict (`$eq`, `$in`, `$ne`, `$nin`, `$or`, `$and`, `$contains`, etc.)
6. **langchain-postgres (>=0.0.17)** → SQL `WHERE` clause (handled by the vector store)

### Optimisations

**Deduplication:** Identical filter objects (even with keys in different order) are removed before processing. For example, `[{"workspaceId": "ws1"}, {"workspaceId": "ws1"}]` is reduced to `[{"workspaceId": "ws1"}]`.

**Same-key grouping:** Multiple OR entries for the same key are collapsed into `$in`:

```python
# Input
[{"workspaceID": "ws1"}, {"workspaceID": "ws2"}, {"workspaceID": "ws3"}]

# Output
{"workspace_id": {"$in": ["ws1", "ws2", "ws3"]}}
```

### JSONB Metadata Filtering

Since langchain-postgres 0.0.17, when a filter references a key that is not a declared `metadata_column`, the library automatically generates a JSONB SQL expression like:

```sql
langchain_metadata->>'docbaseName' = 'value'
```

Nested JSON paths and type casting are also supported natively.

### Public API Reference

The filter utility lives in `src/aviator/utils/query_filter.py` and provides:

| Function | Description |
|----------|-------------|
| `build_search_filter(where)` | Main entry point — returns a MongoDB-style filter for all keys (native + metadata) |
| `build_metadata_post_filter(state_where, query_where)` | Backward-compatible stub — always returns `None` |
| `to_mongodb_filter(where)` | Core conversion from list-of-dicts to MongoDB-style filter dict |
| `merge_filters(*filters)` | Combines multiple filter dicts with AND logic |

### Internal Helpers

| Helper | Description |
|--------|-------------|
| `_is_native_key(key)` | Checks if a key maps to a native DB column |
| `_optimize_same_key_groups(groups)` | Merges same-key `$eq`/`$in` OR entries into `$in` |