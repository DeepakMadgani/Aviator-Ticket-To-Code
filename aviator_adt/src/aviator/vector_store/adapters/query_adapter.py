"""Translate Mongo-style metadata filters into parameterized PostgreSQL queries."""

import ast
import logging
import re
from collections.abc import Iterable
from typing import cast

from aviator.settings import settings
from aviator.vector_store.schema import METADATA_COLUMNS

logger = logging.getLogger(__name__)

_SAFE_FILTER_KEY_RE = re.compile(r"^@?[a-zA-Z_][a-zA-Z0-9_]*$")


def _quote_identifier(name: str) -> str:
    """Safely quote a PostgreSQL identifier."""
    return '"' + name.replace('"', '""') + '"'


class MongoStyleQueryAdapter:
    """Translate Mongo-style filters into parameterized PostgreSQL WHERE clauses."""

    def __init__(self, schema_name: str, table_name: str, vector_cast_type: str = "vector") -> None:
        """Initialize the adapter for a specific schema/table pair."""
        self.schema_name = schema_name
        self.table_name = table_name
        self.vector_cast_type = vector_cast_type

        self.metadata_columns = set(METADATA_COLUMNS)
        self.jsonb_metadata_column = settings.vector_store_metadata_column

    def build_query(
        self,
        filter_dict: dict[str, object] | None,
        query_embedding: list[float],
        limit: int,
    ) -> tuple[str, list[object]]:
        """Build SQL and parameters for vector similarity search with optional filters."""
        embedding = self._embedding_to_vector_literal(query_embedding)

        where_sql = ""
        filter_params: list[object] = []

        if filter_dict:
            where_clause, filter_params = self._build_where_clause(filter_dict)
            if where_clause:
                where_sql = f"WHERE {where_clause}"

        schema_ident = _quote_identifier(self.schema_name)
        table_ident = _quote_identifier(self.table_name)

        sql = f"""
            SELECT
                "langchain_id",
                "{self.jsonb_metadata_column}",
                "content",
                "embedding" <=> %s::{self.vector_cast_type} AS distance
            FROM {schema_ident}.{table_ident}
            {where_sql}
            ORDER BY distance ASC
            LIMIT {int(limit)}
        """

        params: list[object] = [embedding, *filter_params]
        return sql, params

    def build_distinct_document_ids_query(
        self,
        filter_dict: dict[str, object] | None,
    ) -> tuple[str, list[object]]:
        """Build SQL and parameters to fetch distinct document IDs with optional filters."""
        where_sql = ""
        filter_params: list[object] = []

        if filter_dict:
            where_clause, filter_params = self._build_where_clause(filter_dict)
            if where_clause:
                where_sql = f"WHERE {where_clause}"

        schema_ident = _quote_identifier(self.schema_name)
        table_ident = _quote_identifier(self.table_name)

        sql = f"""
            SELECT DISTINCT "document_id"
            FROM {schema_ident}.{table_ident}
            {where_sql}
        """

        return sql, filter_params

    def _embedding_to_vector_literal(self, embedding: object) -> str:
        """Convert an embedding into pgvector literal format."""
        values = self._embedding_to_list(embedding)
        return "[" + ",".join(str(float(v)) for v in values) + "]"

    def _embedding_to_list(self, embedding: object) -> list[float]:
        """Convert embedding input into a list of floats."""
        if isinstance(embedding, list | tuple):
            return [float(v) for v in embedding]

        if hasattr(embedding, "tolist"):
            return [float(v) for v in embedding.tolist()]

        if isinstance(embedding, str):
            parsed = ast.literal_eval(embedding)
            if isinstance(parsed, list | tuple):
                return [float(v) for v in parsed]

            message = "Embedding string must evaluate to a list or tuple"
            raise TypeError(message)

        if isinstance(embedding, Iterable):
            return [float(v) for v in embedding]

        message = f"Unsupported embedding type: {type(embedding)!r}"
        raise TypeError(message)

    def _build_where_clause(self, filter_dict: dict[str, object]) -> tuple[str, list[object]]:
        """Recursively build a SQL WHERE fragment and its bound parameters."""
        if not isinstance(filter_dict, dict):
            message = f"Filter must be a dict, got {type(filter_dict)!r}"
            raise TypeError(message)

        clauses: list[str] = []
        params: list[object] = []

        for key, value in filter_dict.items():
            if key in {"$and", "$or", "$in"}:
                clause, clause_params = self._build_logical_clause(key, value)
                if clause:
                    clauses.append(clause)
                    params.extend(clause_params)
                continue

            if key == "$not":
                if not isinstance(value, dict):
                    message = "$not expects a dict of sub-filters"
                    raise TypeError(message)

                inner_clause, inner_params = self._build_where_clause(cast("dict[str, object]", value))
                if inner_clause:
                    clauses.append(f"NOT ({inner_clause})")
                    params.extend(inner_params)
                continue

            self._validate_filter_key(key)
            field_clause, field_params = self._build_field_clause(key, value)
            clauses.append(field_clause)
            params.extend(field_params)

        return " AND ".join(clauses), params

    def _build_logical_clause(
        self,
        operator: str,
        value: object,
    ) -> tuple[str, list[object]]:
        """Build a logical $and / $or clause."""
        if not isinstance(value, list):
            message = f"{operator} expects a list of sub-filters"
            raise TypeError(message)

        joiner = " AND " if operator == "$and" else " OR "

        sub_clauses: list[str] = []
        sub_params: list[object] = []

        for sub_filter in value:
            if not isinstance(sub_filter, dict):
                message = f"{operator} list items must be dicts, got {type(sub_filter)!r}"
                raise TypeError(message)

            clause, params = self._build_where_clause(cast("dict[str, object]", sub_filter))
            if clause:
                sub_clauses.append(clause)
                sub_params.extend(params)

        if not sub_clauses:
            return "", []

        return f"({joiner.join(sub_clauses)})", sub_params

    def _build_field_clause(self, key: str, value: object) -> tuple[str, list[object]]:
        """Build a clause for a normal field filter."""

        if isinstance(value, dict):
            clauses: list[str] = []
            params: list[object] = []

            for op, op_value in value.items():
                clause, clause_params = self._build_operator_clause(key=key, operator=op, value=op_value)
                clauses.append(clause)
                params.extend(clause_params)

            return " AND ".join(clauses), params

        field_ref = self._field_reference(key, text_mode=True)
        return f"{field_ref} = %s", [str(value)]

    def _build_operator_clause(self, key: str, operator: str, value: object) -> tuple[str, list[object]]:
        """Translate a single field operator into SQL."""

        is_column = key in self.metadata_columns

        if operator == "$contains":
            if not value:
                return "1=0", []

            contains_values: list[object]
            if isinstance(value, str):
                contains_values = [value]
            elif isinstance(value, list | tuple | set):
                contains_values = list(value)
            else:
                message = f"$contains expects a string or iterable, got {type(value)!r}"
                raise TypeError(message)

            if is_column:
                # Native text columns: translate $contains to equality / IN
                str_values = [str(v) for v in contains_values]
                col_ref = self._field_reference(key, text_mode=True)
                if len(str_values) == 1:
                    return f"{col_ref} = %s", str_values
                placeholders = ", ".join(["%s"] * len(str_values))
                return f"{col_ref} IN ({placeholders})", str_values

            json_ref = self._field_reference(key, text_mode=False)
            placeholders = ", ".join(["%s"] * len(contains_values))

            return f"{json_ref} ?& ARRAY[{placeholders}]", [str(v) for v in contains_values]

        elif operator == "$eq":
            field_ref = self._field_reference(key, text_mode=True)
            if is_column:
                return f"{field_ref} = %s", [str(value)]

            if isinstance(value, str):
                return f"\"{self.jsonb_metadata_column}\" @> jsonb_build_object('{key}', %s::text)", [str(value)]

            return f"\"{self.jsonb_metadata_column}\" @> jsonb_build_object('{key}', %s::numeric)", [str(value)]

        else:
            field_ref = self._field_reference(key, text_mode=True)
            match operator:
                case "$ne":
                    # return f"{field_ref} != %s", [str(value)]
                    return f"{field_ref} IS DISTINCT FROM %s", [str(value)]

                case "$lt":
                    if is_column:
                        return f"{field_ref} < %s", [str(value)]
                    return f"({field_ref})::numeric < %s", [value]

                case "$lte":
                    if is_column:
                        return f"{field_ref} <= %s", [str(value)]
                    return f"({field_ref})::numeric <= %s", [value]

                case "$gt":
                    if is_column:
                        return f"{field_ref} > %s", [str(value)]
                    return f"({field_ref})::numeric > %s", [value]

                case "$gte":
                    if is_column:
                        return f"{field_ref} >= %s", [str(value)]
                    return f"({field_ref})::numeric >= %s", [value]

                case "$in":
                    if not isinstance(value, list):
                        message = f"$in for key '{key}' expects a list"
                        raise TypeError(message)

                    if not value:
                        return "1=0", []
                    placeholders = ", ".join(["%s"] * len(value))
                    return f"{field_ref} IN ({placeholders})", [str(v) for v in value]

                case "$nin":
                    if not isinstance(value, list):
                        message = f"$nin for key '{key}' expects a list"
                        raise TypeError(message)

                    if not value:
                        return "1=1", []
                    placeholders = ", ".join(["%s"] * len(value))
                    return f"{field_ref} NOT IN ({placeholders})", [str(v) for v in value]

                case "$exists":
                    if value:
                        if is_column:
                            return f"{key} IS NOT NULL", []
                        return f"{self.jsonb_metadata_column} ? '{key}'", []
                    if is_column:
                        return f"{key} IS NULL", []
                    return f"NOT ({self.jsonb_metadata_column} ? '{key}')", []

                case "$not":
                    if not isinstance(value, dict):
                        message = f"$not for key '{key}' must contain an operator dict"
                        raise TypeError(message)

                    clauses: list[str] = []
                    params: list[object] = []

                    for sub_op, sub_value in cast("dict[str, object]", value).items():
                        clause, clause_params = self._build_operator_clause(key=key, operator=sub_op, value=sub_value)
                        clauses.append(clause)
                        params.extend(clause_params)

                    return f"NOT ({' AND '.join(clauses)})", params

                case _:
                    message = f"Unsupported filter operator '{operator}' for key '{key}'"
                    raise ValueError(message)

    def _field_reference(self, key: str, text_mode: bool) -> str:
        """Return the SQL reference for a field."""
        if key in self.metadata_columns:
            return f'"{key}"'

        operator = "->>" if text_mode else "->"
        return f"\"{self.jsonb_metadata_column}\" {operator} '{key}'"

    def _validate_filter_key(self, key: str) -> None:
        """Validate filter key format."""
        if not _SAFE_FILTER_KEY_RE.match(key):
            message = (
                f"Invalid filter key '{key}': only alphanumeric characters, "
                "underscores, and an optional leading '@' are allowed."
            )
            raise ValueError(message)
