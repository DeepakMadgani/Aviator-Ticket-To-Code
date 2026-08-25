"""Utility for converting ADT where-clause filters to MongoDB-style filters.

The where clause format is a list of filter objects. Each object in the list
is OR'd with the others. Within each object, key-value pairs are AND'd together.

Supported features:
- Simple equality: {"workspaceId": "ws1"}
- Custom metadata keys: {"docbaseName": "doc1"}
- NEQ (not equal): {"_NEQ_": {"documentId": "doc3"}}
- Operator pass-through: {"field": {"$contains": "value"}}

The output is a MongoDB-style filter dict compatible with LangChain's
PGVector and other vector stores that support this query syntax.

**Filter architecture — langchain-postgres v2 (>=0.0.17):**
Only ``workspace_id`` and ``document_id`` exist as real table columns.
Custom metadata keys live inside the ``langchain_metadata`` JSONB column.
Since langchain-postgres 0.0.17, the library natively translates custom
metadata keys to PostgreSQL JSONB ``->>`` expressions.  All filtering
happens at the database level — there is no post-retrieval filtering in
Python.

See: https://python.langchain.com/docs/integrations/vectorstores/pgvector/#filtering-support
"""

import json
import logging
from typing import Any

from aviator.models import WhereClauseReferenceModel
from aviator.settings import settings

logger = logging.getLogger(__name__)

# Mapping from user-facing filter key names to internal column names
KEY_ALIASES: dict[str, str] = {
    "documentId": "document_id",
    "documentID": "document_id",
    "workspaceId": "workspace_id",
    "workspaceID": "workspace_id",
}

# Database columns that langchain-postgres v2 can filter natively.
# Everything else is custom metadata stored in the ``langchain_metadata``
# JSONB column.  Since langchain-postgres >=0.0.17 the library handles
# JSONB metadata filtering natively via ``->>`` SQL expressions.
NATIVE_COLUMNS: frozenset[str] = frozenset({"workspace_id", "document_id"})


def build_search_filter(
    state_where_clauses: list[WhereClauseReferenceModel] | None = None,
    query_where_clauses: list[WhereClauseReferenceModel] | None = None,
) -> dict[str, Any] | None:
    """Build a MongoDB-style search filter for the vector store.

    ``state_where_clauses`` is authoritative when non-empty (scope is fixed by the API
    caller).  ``query_where_clauses`` (LLM-generated) is used only when no state scope
    has been defined.

    Args:
        state_where_clauses: Authoritative scope from the API request.
        query_where_clauses: LLM-generated scope, used as fallback.

    Returns:
        MongoDB-style filter dict, or ``None``.

    """

    where_clauses: list[WhereClauseReferenceModel] | None = None

    if state_where_clauses and query_where_clauses:
        where_clauses = state_where_clauses.copy()
        for query_clause in query_where_clauses:
            if query_clause in state_where_clauses:
                continue
            # llm hallucinates and treats other ids as document/workspace ids, when state queries are defined, we ignore those llm generated clauses
            if not query_clause.document_id and not query_clause.workspace_id:
                where_clauses.append(query_clause)

    elif not query_where_clauses:
        where_clauses = state_where_clauses

    elif not state_where_clauses:
        where_clauses = query_where_clauses

    else:
        return None

    filters = _to_mongodb_filter(where_clauses=where_clauses)

    if settings.dev_tools:
        # this logging is only for development purpose. it exposes customer data in logs. #
        logger.info("Built search filter: %s", json.dumps(filters, indent=2))

    return filters


def build_metadata_post_filter(
    state_where: list[dict[str, Any]] | None = None,
    query_where: list[dict[str, Any]] | None = None,
) -> None:
    """Build a post-retrieval filter function for custom metadata keys.

    .. deprecated::
        All metadata filtering is now pushed to the database via JSONB
        expressions.  This function always returns ``None``.  It is kept
        for backward compatibility with existing callers.

    Args:
        state_where: State-level where clause (unused).
        query_where: Query-level where clause (unused).

    Returns:
        Always ``None``.

    """


def _resolve_key(key: str) -> str:
    """Resolve a user-facing key name to the internal column/metadata name."""
    return KEY_ALIASES.get(key, key)


def _is_native_key(key: str) -> bool:
    """Check if *key* (after alias resolution) maps to a native DB column."""
    return _resolve_key(key) in NATIVE_COLUMNS


def _build_single_condition(key: str, value: Any) -> dict[str, Any]:  # noqa: ANN401
    """Build a single MongoDB-style condition for a key-value pair.

    Args:
        key: The resolved filter key name.
        value: The filter value - can be a string, list, or dict with operators.

    Returns:
        A MongoDB-style filter condition dict.

    """
    if isinstance(value, dict):
        # Operator-style value, e.g. {"$contains": "val"}
        return {key: value}

    if isinstance(value, list):
        # Array value -> $in operator
        return {key: {"$in": value}}

    # Simple equality
    return {key: {"$eq": value}}


def _build_neq_conditions(neq_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Build NOT-EQUAL conditions from a _NEQ_ dict.

    Args:
        neq_dict: Dict of key-value pairs that should be negated.

    Returns:
        List of MongoDB-style $ne conditions.

    """
    conditions = []
    for key, value in neq_dict.items():
        resolved = _resolve_key(key)
        if isinstance(value, list):
            # NEQ for array -> $nin
            conditions.append({resolved: {"$nin": value}})
        else:
            conditions.append({resolved: {"$ne": value}})
    return conditions


def _build_filter_group(filter_obj: dict[str, Any]) -> dict[str, Any] | None:
    """Build a single AND-group from one filter object.

    All key-value pairs within a single filter object are AND'd together,
    except explicit logical operators like $or / $and which are preserved.
    """
    conditions: list[dict[str, Any]] = []

    for key, value in filter_obj.items():
        if key in {"_NEQ_", "$ne"}:
            if isinstance(value, dict):
                conditions.extend(_build_neq_conditions(value))
            else:
                logger.warning("%s value must be a dict, got %s - skipping", key, type(value))
            continue

        if key in {"$or", "$and"}:
            if not isinstance(value, list):
                logger.warning("%s value must be a list, got %s - skipping", key, type(value))
                continue

            nested_conditions: list[dict[str, Any]] = []
            for item in value:
                if not isinstance(item, dict):
                    logger.warning("%s item must be a dict, got %s - skipping", key, type(item))
                    continue

                nested = _build_filter_group(item)
                if nested is not None:
                    nested_conditions.append(nested)

            if nested_conditions:
                conditions.append({key: nested_conditions})
            continue

        resolved = _resolve_key(key)
        conditions.append(_build_single_condition(resolved, value))

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]

    return {"$and": conditions}


def _optimize_same_key_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge single-key ``$eq`` / ``$in`` OR entries for the same field into ``$in``.

    Example::

        [{"ws": {"$eq": "a"}}, {"ws": {"$eq": "b"}}]
        → [{"ws": {"$in": ["a", "b"]}}]

    Multi-key groups and groups with operators other than ``$eq`` / ``$in``
    are left unchanged.
    """
    key_values: dict[str, list[Any]] = {}
    key_first_idx: dict[str, int] = {}
    non_mergeable: list[tuple[int, dict[str, Any]]] = []

    for idx, group in enumerate(groups):
        if len(group) == 1:
            field = next(iter(group))
            val = group[field]
            if isinstance(val, dict) and len(val) == 1:
                if "$eq" in val:
                    key_values.setdefault(field, []).append(val["$eq"])
                    key_first_idx.setdefault(field, idx)
                    continue
                if "$in" in val and isinstance(val["$in"], list):
                    key_values.setdefault(field, []).extend(val["$in"])
                    key_first_idx.setdefault(field, idx)
                    continue
        non_mergeable.append((idx, group))

    # Build merged entries positioned at their first occurrence index
    merged_entries: list[tuple[int, dict[str, Any]]] = []
    for field, values in key_values.items():
        pos = key_first_idx[field]
        unique_values = list(dict.fromkeys(values))  # deduplicate, preserve order
        if len(unique_values) == 1:
            merged_entries.append((pos, {field: {"$eq": unique_values[0]}}))
        else:
            merged_entries.append((pos, {field: {"$in": unique_values}}))

    # Combine and sort by original position
    all_entries = non_mergeable + merged_entries
    all_entries.sort(key=lambda x: x[0])
    return [entry for _, entry in all_entries]


def _to_mongodb_filter(where_clauses: list[WhereClauseReferenceModel] | None = None) -> dict[str, Any] | None:
    """Convert an ADT where-clause list to a MongoDB-style filter dict.

    The where clause is a list of filter objects.  Positive (non-``_NEQ_``)
    objects are OR'd with each other.  Within each object, conditions are
    AND'd.  **Pure ``_NEQ_`` entries** (objects whose only key is ``_NEQ_``)
    are treated as **global AND conditions** — they restrict every OR branch,
    not just one of them.

    Before processing, the input list is deduplicated and same-key single-
    value OR entries are collapsed into ``$in``.

    Examples:
        >>> to_mongodb_filter([{"workspaceId": "ws1"}, {"documentId": "doc1"}])
        {"$or": [{"workspace_id": {"$eq": "ws1"}}, {"document_id": {"$eq": "doc1"}}]}

        >>> to_mongodb_filter([{"workspaceId": "ws1", "documentId": "doc1"}])
        {"$and": [{"workspace_id": {"$eq": "ws1"}}, {"document_id": {"$eq": "doc1"}}]}

        >>> to_mongodb_filter([{"workspaceId": "ws1"}, {"workspaceId": "ws2"}])
        {"workspace_id": {"$in": ["ws1", "ws2"]}}

        >>> to_mongodb_filter([{"workspaceId": "ws1"}, {"_NEQ_": {"documentId": "doc3"}}])
        {"$and": [{"workspace_id": {"$eq": "ws1"}}, {"document_id": {"$ne": "doc3"}}]}

        Mixed individual entries with a multi-key AND group:

        >>> to_mongodb_filter([{"workspaceId": "ws1"}, {"documentId": "doc1"}, {"workspaceId": "ws2", "documentId": "doc2"}])
        {"$or": [{"workspace_id": {"$eq": "ws1"}}, {"document_id": {"$eq": "doc1"}}, {"$and": [{"workspace_id": {"$eq": "ws2"}}, {"document_id": {"$eq": "doc2"}}]}]}

    Args:
        where_clauses: List of filter objects, or None.

    Returns:
        MongoDB-style filter dict, or None if no filters.

    """
    if not where_clauses:
        return None

    positive_groups: list[dict[str, Any]] = []
    global_neq_conditions: list[dict[str, Any]] = []

    for where_clause in where_clauses:
        if not isinstance(where_clause, WhereClauseReferenceModel):
            logger.warning("Filter object must be a WhereClauseReferenceModel, got %s - skipping", type(where_clause))
            continue

        filter_obj = where_clause.model_dump(exclude_none=True)

        # Pure _NEQ_ entries → global AND conditions
        non_neq_keys = [k for k in filter_obj if k not in {"_NEQ_", "$ne"}]
        if not non_neq_keys and ("_NEQ_" in filter_obj or "$ne" in filter_obj):
            neq_val = filter_obj.get("_NEQ_") or filter_obj.get("$ne")
            if isinstance(neq_val, dict):
                global_neq_conditions.extend(_build_neq_conditions(neq_val))
            else:
                logger.warning("_NEQ_ or $ne value must be a dict, got %s - skipping", type(neq_val))
            continue

        group = _build_filter_group(filter_obj)
        if group is not None:
            positive_groups.append(group)

    # 2. Optimise: merge single-key OR entries for the same key into $in
    if len(positive_groups) > 1:
        positive_groups = _optimize_same_key_groups(positive_groups)

    # 3. Build OR part from positive groups
    or_part: dict[str, Any] | None = None
    if len(positive_groups) == 1:
        or_part = positive_groups[0]
    elif len(positive_groups) > 1:
        or_part = {"$or": positive_groups}

    # 4. Combine OR part with global NEQ conditions
    all_parts: list[dict[str, Any]] = []
    if or_part:
        all_parts.append(or_part)
    all_parts.extend(global_neq_conditions)

    if not all_parts:
        return None
    if len(all_parts) == 1:
        return all_parts[0]
    return {"$and": all_parts}
