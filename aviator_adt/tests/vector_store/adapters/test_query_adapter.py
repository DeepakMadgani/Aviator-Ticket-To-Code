"""Test suite for PGVector adapter."""

import types

import pytest

import aviator.vector_store.adapters.query_adapter as target
from aviator.vector_store.adapters.query_adapter import MongoStyleQueryAdapter, _quote_identifier


class DummyArray:
    """Simple test double that mimics array objects exposing tolist()."""

    def __init__(self, values):
        """Store values to return from tolist()."""
        self._values = values

    def tolist(self):
        return self._values


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(
        target,
        "METADATA_COLUMNS",
        {"document_id", "workspace_id", "score", "created_at"},
        raising=False,
    )
    monkeypatch.setattr(
        target,
        "settings",
        types.SimpleNamespace(vector_store_metadata_column="langchain_metadata"),
        raising=False,
    )
    return MongoStyleQueryAdapter(schema_name="public", table_name="aviator")


# -------------------------
# _quote_identifier
# -------------------------


def test_quote_identifier_simple():
    assert _quote_identifier("public") == '"public"'


def test_quote_identifier_escapes_embedded_quotes():
    assert _quote_identifier('my"table') == '"my""table"'


# -------------------------
# _embedding_to_list
# -------------------------


def test_embedding_to_list_from_list(adapter):
    assert adapter._embedding_to_list([1, 2, 3]) == [1.0, 2.0, 3.0]


def test_embedding_to_list_from_tuple(adapter):
    assert adapter._embedding_to_list((1, 2, 3)) == [1.0, 2.0, 3.0]


def test_embedding_to_list_from_object_with_tolist(adapter):
    arr = DummyArray([1, 2.5, 3])
    assert adapter._embedding_to_list(arr) == [1.0, 2.5, 3.0]


def test_embedding_to_list_from_string_literal(adapter):
    assert adapter._embedding_to_list("[1, 2, 3.5]") == [1.0, 2.0, 3.5]


def test_embedding_to_list_from_iterable(adapter):
    gen = (x for x in [1, 2, 3])
    assert adapter._embedding_to_list(gen) == [1.0, 2.0, 3.0]


def test_embedding_to_list_raises_for_unsupported_type(adapter):
    with pytest.raises(TypeError, match="Unsupported embedding type"):
        adapter._embedding_to_list(123)


# -------------------------
# _embedding_to_vector_literal
# -------------------------


def test_embedding_to_vector_literal(adapter):
    assert adapter._embedding_to_vector_literal([1, 2.5, 3]) == "[1.0,2.5,3.0]"


# -------------------------
# _field_reference
# -------------------------


def test_field_reference_for_metadata_column_text_mode(adapter):
    assert adapter._field_reference("document_id", text_mode=True) == '"document_id"'


def test_field_reference_for_metadata_column_json_mode(adapter):
    assert adapter._field_reference("document_id", text_mode=False) == '"document_id"'


def test_field_reference_for_jsonb_key_text_mode(adapter):
    assert adapter._field_reference("docbaseName", text_mode=True) == "\"langchain_metadata\" ->> 'docbaseName'"


def test_field_reference_for_jsonb_key_json_mode(adapter):
    assert adapter._field_reference("containerID", text_mode=False) == "\"langchain_metadata\" -> 'containerID'"


# -------------------------
# _validate_filter_key
# -------------------------


@pytest.mark.parametrize(
    "key",
    [
        "docbaseName",
        "containerID",
        "workspace_id",
        "@container_id",
    ],
)
def test_validate_filter_key_accepts_safe_keys(adapter, key):
    adapter._validate_filter_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "x;y",
        "field-name",
        "field$",
        "drop table users",
        "a.b",
        "x)",
    ],
)
def test_validate_filter_key_rejects_unsafe_keys(adapter, key):
    with pytest.raises(ValueError, match="Invalid filter key"):
        adapter._validate_filter_key(key)


# -------------------------
# _build_operator_clause
# -------------------------


def test_build_operator_clause_contains_single_string(adapter):
    clause, params = adapter._build_operator_clause("containerID", "$contains", "abc")
    assert clause == "\"langchain_metadata\" -> 'containerID' ?& ARRAY[%s]"
    assert params == ["abc"]


def test_build_operator_clause_contains_list(adapter):
    clause, params = adapter._build_operator_clause("containerID", "$contains", ["a", "b"])
    assert clause == "\"langchain_metadata\" -> 'containerID' ?& ARRAY[%s, %s]"
    assert params == ["a", "b"]


def test_build_operator_clause_contains_empty_value_returns_false_clause(adapter):
    clause, params = adapter._build_operator_clause("containerID", "$contains", [])
    assert clause == "1=0"
    assert params == []


def test_build_operator_clause_contains_native_column_single(adapter):
    clause, params = adapter._build_operator_clause("workspace_id", "$contains", "ws-1")
    assert clause == '"workspace_id" = %s'
    assert params == ["ws-1"]


def test_build_operator_clause_contains_native_column_list(adapter):
    clause, params = adapter._build_operator_clause("workspace_id", "$contains", ["ws-1", "ws-2"])
    assert clause == '"workspace_id" IN (%s, %s)'
    assert params == ["ws-1", "ws-2"]


def test_build_operator_clause_eq_for_metadata_column(adapter):
    clause, params = adapter._build_operator_clause("document_id", "$eq", "doc-1")
    assert clause == '"document_id" = %s'
    assert params == ["doc-1"]


def test_build_operator_clause_eq_for_jsonb_string(adapter):
    clause, params = adapter._build_operator_clause("docbaseName", "$eq", "testdoc")
    assert clause == "\"langchain_metadata\" @> jsonb_build_object('docbaseName', %s::text)"
    assert params == ["testdoc"]


def test_build_operator_clause_eq_for_jsonb_numeric(adapter):
    clause, params = adapter._build_operator_clause("score", "$eq", 10)
    # "score" is in METADATA_COLUMNS in fixture, so this will behave like a column
    assert clause == '"score" = %s'
    assert params == ["10"]


def test_build_operator_clause_eq_for_non_column_numeric_jsonb(adapter):
    clause, params = adapter._build_operator_clause("rank", "$eq", 10)
    assert clause == "\"langchain_metadata\" @> jsonb_build_object('rank', %s::numeric)"
    assert params == ["10"]


@pytest.mark.parametrize(
    ("operator", "expected_sql"),
    [
        ("$ne", '"document_id" IS DISTINCT FROM %s'),
        ("$lt", '"document_id" < %s'),
        ("$lte", '"document_id" <= %s'),
        ("$gt", '"document_id" > %s'),
        ("$gte", '"document_id" >= %s'),
    ],
)
def test_build_operator_clause_comparison_for_metadata_column(adapter, operator, expected_sql):
    clause, params = adapter._build_operator_clause("document_id", operator, "abc")
    assert clause == expected_sql
    assert params == ["abc"]


@pytest.mark.parametrize(
    ("operator", "expected_sql"),
    [
        ("$lt", "(\"langchain_metadata\" ->> 'rank')::numeric < %s"),
        ("$lte", "(\"langchain_metadata\" ->> 'rank')::numeric <= %s"),
        ("$gt", "(\"langchain_metadata\" ->> 'rank')::numeric > %s"),
        ("$gte", "(\"langchain_metadata\" ->> 'rank')::numeric >= %s"),
    ],
)
def test_build_operator_clause_comparison_for_jsonb_numeric(adapter, operator, expected_sql):
    clause, params = adapter._build_operator_clause("rank", operator, 7)
    assert clause == expected_sql
    assert params == [7]


def test_build_operator_clause_in(adapter):
    clause, params = adapter._build_operator_clause("document_id", "$in", ["a", "b", "c"])
    assert clause == '"document_id" IN (%s, %s, %s)'
    assert params == ["a", "b", "c"]


def test_build_operator_clause_in_empty_returns_false_clause(adapter):
    clause, params = adapter._build_operator_clause("document_id", "$in", [])
    assert clause == "1=0"
    assert params == []


def test_build_operator_clause_nin(adapter):
    clause, params = adapter._build_operator_clause("document_id", "$nin", ["a", "b"])
    assert clause == '"document_id" NOT IN (%s, %s)'
    assert params == ["a", "b"]


def test_build_operator_clause_nin_empty_returns_true_clause(adapter):
    clause, params = adapter._build_operator_clause("document_id", "$nin", [])
    assert clause == "1=1"
    assert params == []


def test_build_operator_clause_exists_true_for_metadata_column(adapter):
    clause, params = adapter._build_operator_clause("document_id", "$exists", True)
    assert clause == "document_id IS NOT NULL"
    assert params == []


def test_build_operator_clause_exists_false_for_metadata_column(adapter):
    clause, params = adapter._build_operator_clause("document_id", "$exists", False)
    assert clause == "document_id IS NULL"
    assert params == []


def test_build_operator_clause_exists_true_for_jsonb_key(adapter):
    clause, params = adapter._build_operator_clause("docbaseName", "$exists", True)
    assert clause == "langchain_metadata ? 'docbaseName'"
    assert params == []


def test_build_operator_clause_exists_false_for_jsonb_key(adapter):
    clause, params = adapter._build_operator_clause("docbaseName", "$exists", False)
    assert clause == "NOT (langchain_metadata ? 'docbaseName')"
    assert params == []


def test_build_operator_clause_not_wraps_suboperators(adapter):
    clause, params = adapter._build_operator_clause(
        "rank",
        "$not",
        {"$gt": 5, "$lt": 10},
    )
    assert (
        clause
        == "NOT ((\"langchain_metadata\" ->> 'rank')::numeric > %s AND (\"langchain_metadata\" ->> 'rank')::numeric < %s)"
    )
    assert params == [5, 10]


def test_build_operator_clause_not_requires_dict(adapter):
    with pytest.raises(TypeError, match=r"\$not for key 'rank' must contain an operator dict"):
        adapter._build_operator_clause("rank", "$not", 123)


def test_build_operator_clause_unsupported_operator(adapter):
    with pytest.raises(ValueError, match="Unsupported filter operator"):
        adapter._build_operator_clause("rank", "$regex", "abc")


# -------------------------
# _build_field_clause
# -------------------------


def test_build_field_clause_plain_scalar_uses_text_comparison(adapter):
    clause, params = adapter._build_field_clause("docbaseName", "testdoc")
    assert clause == "\"langchain_metadata\" ->> 'docbaseName' = %s"
    assert params == ["testdoc"]


def test_build_field_clause_multiple_operators_joined_with_and(adapter):
    clause, params = adapter._build_field_clause("rank", {"$gte": 1, "$lte": 10})
    assert (
        clause
        == "(\"langchain_metadata\" ->> 'rank')::numeric >= %s AND (\"langchain_metadata\" ->> 'rank')::numeric <= %s"
    )
    assert params == [1, 10]


# -------------------------
# _build_logical_clause
# -------------------------


def test_build_logical_clause_and(adapter):
    clause, params = adapter._build_logical_clause(
        "$and",
        [
            {"document_id": {"$eq": "a"}},
            {"rank": {"$gt": 10}},
        ],
    )
    assert clause == '("document_id" = %s AND ("langchain_metadata" ->> \'rank\')::numeric > %s)'
    assert params == ["a", 10]


def test_build_logical_clause_or(adapter):
    clause, params = adapter._build_logical_clause(
        "$or",
        [
            {"document_id": {"$eq": "a"}},
            {"document_id": {"$eq": "b"}},
        ],
    )
    assert clause == '("document_id" = %s OR "document_id" = %s)'
    assert params == ["a", "b"]


def test_build_logical_clause_rejects_non_list(adapter):
    with pytest.raises(TypeError, match=r"\$and expects a list of sub-filters"):
        adapter._build_logical_clause("$and", {"document_id": "a"})


def test_build_logical_clause_skips_empty_subclauses(adapter):
    clause, params = adapter._build_logical_clause("$and", [{}])
    assert clause == ""
    assert params == []


# -------------------------
# _build_where_clause
# -------------------------


def test_build_where_clause_rejects_non_dict(adapter):
    with pytest.raises(TypeError, match="Filter must be a dict"):
        adapter._build_where_clause(["not-a-dict"])


def test_build_where_clause_with_plain_fields_and_operator_fields(adapter):
    clause, params = adapter._build_where_clause(
        {
            "docbaseName": "testdoc",
            "document_id": {"$eq": "doc-1"},
        }
    )
    assert clause == '"langchain_metadata" ->> \'docbaseName\' = %s AND "document_id" = %s'
    assert params == ["testdoc", "doc-1"]


def test_build_where_clause_with_top_level_not(adapter):
    clause, params = adapter._build_where_clause(
        {
            "$not": {
                "document_id": {"$eq": "doc-1"},
            }
        }
    )
    assert clause == 'NOT ("document_id" = %s)'
    assert params == ["doc-1"]


def test_build_where_clause_with_nested_and_or_not(adapter):
    clause, params = adapter._build_where_clause(
        {
            "$and": [
                {"docbaseName": {"$eq": "testdoc"}},
                {
                    "$or": [
                        {"containerID": {"$contains": ["a", "b"]}},
                        {"rank": {"$gte": 5}},
                    ]
                },
                {
                    "$not": {
                        "document_id": {"$in": ["x", "y"]},
                    }
                },
            ]
        }
    )

    assert "\"langchain_metadata\" @> jsonb_build_object('docbaseName', %s::text)" in clause
    assert "\"langchain_metadata\" -> 'containerID' ?& ARRAY[%s, %s]" in clause
    assert "(\"langchain_metadata\" ->> 'rank')::numeric >= %s" in clause
    assert 'NOT ("document_id" IN (%s, %s))' in clause

    assert " AND " in clause
    assert " OR " in clause

    assert params == ["testdoc", "a", "b", 5, "x", "y"]


def test_build_where_clause_rejects_invalid_filter_key(adapter):
    with pytest.raises(ValueError, match="Invalid filter key"):
        adapter._build_where_clause({"bad-key": "value"})


# -------------------------
# build_query
# -------------------------


def test_build_query_without_filter(adapter):
    sql, params = adapter.build_query(
        filter_dict={},
        query_embedding=[1, 2, 3],
        limit=5,
    )

    assert 'FROM "public"."aviator"' in sql
    assert '"embedding" <=> %s::vector AS distance' in sql
    assert "WHERE" not in sql
    assert "LIMIT 5" in sql
    assert params == ["[1.0,2.0,3.0]"]


def test_build_query_with_filter(adapter):
    sql, params = adapter.build_query(
        filter_dict={"document_id": {"$eq": "doc-123"}},
        query_embedding=[0.1, 0.2],
        limit=10,
    )

    assert 'FROM "public"."aviator"' in sql
    assert 'WHERE "document_id" = %s' in sql
    assert "ORDER BY distance ASC" in sql
    assert "LIMIT 10" in sql
    assert params == ["[0.1,0.2]", "doc-123"]


def test_build_query_casts_limit_to_int(adapter):
    sql, params = adapter.build_query(
        filter_dict={},
        query_embedding=[1, 2],
        limit="20",
    )

    assert "LIMIT 20" in sql
    assert params == ["[1.0,2.0]"]


def test_build_query_with_complex_filter(adapter):
    sql, params = adapter.build_query(
        filter_dict={
            "$and": [
                {"docbaseName": {"$eq": "testdoc"}},
                {"containerID": {"$contains": ["0b01", "0c01"]}},
                {"rank": {"$gte": 3}},
            ]
        },
        query_embedding=(1, 2, 3),
        limit=7,
    )

    assert "WHERE" in sql
    assert "\"langchain_metadata\" @> jsonb_build_object('docbaseName', %s::text)" in sql
    assert "\"langchain_metadata\" -> 'containerID' ?& ARRAY[%s, %s]" in sql
    assert "(\"langchain_metadata\" ->> 'rank')::numeric >= %s" in sql
    assert params == ["[1.0,2.0,3.0]", "testdoc", "0b01", "0c01", 3]


def test_build_query_uses_halfvec_cast_when_configured(monkeypatch):
    monkeypatch.setattr(
        target,
        "settings",
        types.SimpleNamespace(vector_store_metadata_column="langchain_metadata"),
        raising=False,
    )
    halfvec_adapter = MongoStyleQueryAdapter(
        schema_name="public",
        table_name="aviator",
        vector_cast_type="halfvec",
    )

    sql, params = halfvec_adapter.build_query(
        filter_dict={},
        query_embedding=[1, 2, 3],
        limit=5,
    )

    assert '"embedding" <=> %s::halfvec AS distance' in sql
    assert params == ["[1.0,2.0,3.0]"]


# -------------------------
# build_distinct_document_ids_query
# -------------------------


class TestBuildDistinctDocumentIdsQuery:
    """Tests for build_distinct_document_ids_query."""

    def test_no_filter_returns_select_distinct(self, adapter):
        sql, params = adapter.build_distinct_document_ids_query(filter_dict=None)
        assert 'SELECT DISTINCT "document_id"' in sql
        assert '"public"."aviator"' in sql
        assert "WHERE" not in sql
        assert params == []

    def test_empty_filter_returns_no_where(self, adapter):
        sql, params = adapter.build_distinct_document_ids_query(filter_dict={})
        assert "WHERE" not in sql
        assert params == []

    def test_workspace_id_eq_filter(self, adapter):
        sql, params = adapter.build_distinct_document_ids_query(filter_dict={"workspace_id": {"$eq": "ws-1"}})
        assert "WHERE" in sql
        assert '"workspace_id" = %s' in sql
        assert params == ["ws-1"]

    def test_document_id_in_filter(self, adapter):
        sql, params = adapter.build_distinct_document_ids_query(
            filter_dict={"document_id": {"$in": ["doc-1", "doc-2"]}}
        )
        assert "WHERE" in sql
        assert '"document_id" IN (%s, %s)' in sql
        assert params == ["doc-1", "doc-2"]

    def test_jsonb_metadata_filter(self, adapter):
        sql, params = adapter.build_distinct_document_ids_query(filter_dict={"customKey": {"$eq": "val1"}})
        assert "WHERE" in sql
        assert "langchain_metadata" in sql
        assert params == ["val1"]

    def test_and_filter(self, adapter):
        sql, params = adapter.build_distinct_document_ids_query(
            filter_dict={
                "$and": [
                    {"workspace_id": {"$eq": "ws-1"}},
                    {"document_id": {"$eq": "doc-1"}},
                ]
            }
        )
        assert "WHERE" in sql
        assert '"workspace_id" = %s' in sql
        assert '"document_id" = %s' in sql
        assert params == ["ws-1", "doc-1"]
