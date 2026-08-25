"""Tests for the query filter utility module."""

import pytest

from aviator.models import WhereClauseReferenceModel
from aviator.utils.search_filter import (
    _is_native_key,
    _optimize_same_key_groups,
    _to_mongodb_filter,
    build_metadata_post_filter,
    build_search_filter,
)


def _build_where_clauses(clauses: list[dict]) -> list[WhereClauseReferenceModel]:
    """Build where clauses for tests."""
    return [WhereClauseReferenceModel(**clause) for clause in clauses if clause is not None]


class TestToMongodbFilter:
    """Tests for to_mongodb_filter function."""

    def test_none_input(self):
        assert _to_mongodb_filter(None) is None

    def test_empty_list(self):
        assert _to_mongodb_filter([]) is None

    # --- Ticket Sample 1: OR of two simple filters ---
    def test_sample1_or_workspace_document(self):
        """Test OR of workspace and document filters."""
        where = _build_where_clauses([{"workspaceID": "ws1"}, {"documentID": "doc1"}])
        result = _to_mongodb_filter(where)
        assert result == {
            "$or": [
                {"workspace_id": {"$eq": "ws1"}},
                {"document_id": {"$eq": "doc1"}},
            ]
        }

    # --- Ticket Sample 2: AND of workspace and document ---
    def test_sample2_and_workspace_document(self):
        """Test AND of workspace and document in single filter object."""
        where = _build_where_clauses([{"workspaceID": "ws1", "documentID": "doc1"}])
        result = _to_mongodb_filter(where)
        assert result == {
            "$and": [
                {"workspace_id": {"$eq": "ws1"}},
                {"document_id": {"$eq": "doc1"}},
            ]
        }

    # --- Ticket Sample 3: Multiple workspace IDs via separate objects ---
    def test_sample3_multiple_workspace_ids(self):
        """Test multiple workspace IDs as separate OR objects merged into $in."""
        where = _build_where_clauses([{"workspaceID": "ws1"}, {"workspaceID": "ws2"}])
        result = _to_mongodb_filter(where)
        assert result == {"workspace_id": {"$in": ["ws1", "ws2"]}}

    # --- Ticket Sample 4: Custom metadata keys ---
    def test_sample4_custom_metadata_keys(self):
        """Test custom metadata keys like docbaseName and containerID."""
        where = _build_where_clauses([{"docbaseName": "doc1"}, {"containerID": "cont1"}])
        result = _to_mongodb_filter(where)
        assert result == {
            "$or": [
                {"docbaseName": {"$eq": "doc1"}},
                {"containerID": {"$eq": "cont1"}},
            ]
        }

    # --- Ticket Sample 5: NEQ ---
    def test_sample5_neq(self):
        """Test NEQ operator — treated as global AND, not OR branch."""
        where = _build_where_clauses(
            [
                {"workspaceID": "ws1"},
                {"workspaceID": "ws2"},
                {"_NEQ_": {"documentID": "doc3"}},
            ]
        )
        result = _to_mongodb_filter(where)
        # NEQ is AND'd globally; same-key OR entries merged into $in
        assert result == {
            "$and": [
                {"workspace_id": {"$in": ["ws1", "ws2"]}},
                {"document_id": {"$ne": "doc3"}},
            ]
        }

    # --- Single filter, single key (no $or needed) ---
    def test_single_filter_single_key(self):
        where = _build_where_clauses([{"workspaceID": "ws1"}])
        result = _to_mongodb_filter(where)
        assert result == {"workspace_id": {"$eq": "ws1"}}

    # --- $contains operator ---
    def test_contains_operator(self):
        where = _build_where_clauses([{"tags": {"$contains": "important"}}])
        result = _to_mongodb_filter(where)
        assert result == {"tags": {"$contains": "important"}}

    # --- NEQ with array value -> $nin ---
    def test_neq_array_value(self):
        where = _build_where_clauses([{"_NEQ_": {"documentID": ["doc1", "doc2"]}}])
        result = _to_mongodb_filter(where)
        assert result == {"document_id": {"$nin": ["doc1", "doc2"]}}

    # --- NEQ combined with equality in same object ---
    def test_neq_with_equality_in_same_object(self):
        where = _build_where_clauses([{"workspaceID": "ws1", "_NEQ_": {"documentID": "doc3"}}])
        result = _to_mongodb_filter(where)
        assert result == {
            "$and": [
                {"workspace_id": {"$eq": "ws1"}},
                {"document_id": {"$ne": "doc3"}},
            ]
        }

    # --- Multiple NEQ keys ---
    def test_neq_multiple_keys(self):
        where = _build_where_clauses([{"_NEQ_": {"documentID": "doc1", "workspaceID": "ws2"}}])
        result = _to_mongodb_filter(where)
        assert result == {
            "$and": [
                {"document_id": {"$ne": "doc1"}},
                {"workspace_id": {"$ne": "ws2"}},
            ]
        }

    # --- Complex: mix of AND, OR, NEQ, custom ---
    def test_complex_mixed_filters(self):
        where = _build_where_clauses(
            [
                {"workspaceID": "ws1", "docbaseName": "myDocbase"},
                {"containerID": "cont1"},
                {"_NEQ_": {"documentID": "excluded"}},
            ]
        )
        result = _to_mongodb_filter(where)
        # NEQ separated as global AND; positive groups are OR'd
        assert result == {
            "$and": [
                {
                    "$or": [
                        {
                            "$and": [
                                {"workspace_id": {"$eq": "ws1"}},
                                {"docbaseName": {"$eq": "myDocbase"}},
                            ]
                        },
                        {"containerID": {"$eq": "cont1"}},
                    ]
                },
                {"document_id": {"$ne": "excluded"}},
            ]
        }

    # --- Edge cases ---
    def test_empty_filter_object_skipped(self):
        where = [{}]
        result = _to_mongodb_filter(where)
        assert result is None

    def test_neq_with_non_dict_value_skipped(self):
        where = _build_where_clauses([{"_NEQ_": "invalid"}])
        result = _to_mongodb_filter(where)
        assert result is None

    @pytest.mark.parametrize(
        ("alias", "expected_key", "value"),
        [("documentID", "document_id", "doc1"), ("workspaceID", "workspace_id", "ws1")],
    )
    def test_backward_compat_aliases(self, alias, expected_key, value):
        """Test that legacy aliases (documentID, workspaceID) map correctly."""
        result = _to_mongodb_filter(_build_where_clauses([{alias: value}]))
        assert result == {expected_key: {"$eq": value}}

    # --- Same-key OR grouping optimisation ---
    def test_same_key_mixed_with_different_key(self):
        """Same-key grouping works alongside different-key groups."""
        where = _build_where_clauses([{"workspaceID": "ws1"}, {"workspaceID": "ws2"}, {"documentID": "doc1"}])
        result = _to_mongodb_filter(where)
        assert result == {
            "$or": [
                {"workspace_id": {"$in": ["ws1", "ws2"]}},
                {"document_id": {"$eq": "doc1"}},
            ]
        }

    def test_mixed_individual_entries_with_and_group(self):
        """Test mixed individual entries alongside a multi-key AND group."""
        where = _build_where_clauses(
            [{"workspaceID": "ws1"}, {"documentID": "doc1"}, {"workspaceID": "ws2", "documentID": "doc2"}]
        )
        result = _to_mongodb_filter(where)
        assert result == {
            "$or": [
                {"workspace_id": {"$eq": "ws1"}},
                {"document_id": {"$eq": "doc1"}},
                {"$and": [{"workspace_id": {"$eq": "ws2"}}, {"document_id": {"$eq": "doc2"}}]},
            ]
        }

    def test_four_separate_dicts_same_keys_merged(self):
        """Four separate single-key dicts with overlapping keys are merged via $in."""
        where = _build_where_clauses(
            [
                {"workspaceID": "ws1"},
                {"documentID": "doc1"},
                {"workspaceID": "ws2"},
                {"documentID": "doc2"},
            ]
        )
        result = _to_mongodb_filter(where)
        # Same-key entries are collapsed: ws1+ws2 → $in, doc1+doc2 → $in
        assert result == {
            "$or": [
                {"workspace_id": {"$in": ["ws1", "ws2"]}},
                {"document_id": {"$in": ["doc1", "doc2"]}},
            ]
        }

    def test_single_same_key_stays_eq(self):
        """A lone entry after dedup still uses $eq, not $in."""
        where = _build_where_clauses([{"workspaceID": "ws1"}, {"workspaceID": "ws1"}])
        result = _to_mongodb_filter(where)
        assert result == {"workspace_id": {"$eq": "ws1"}}

    # --- NEQ as global AND ---
    def test_neq_only_multiple_entries(self):
        """Multiple standalone _NEQ_ entries are all AND'd globally."""
        where = _build_where_clauses(
            [
                {"workspaceID": "ws1"},
                {"_NEQ_": {"documentID": "doc1"}},
                {"_NEQ_": {"documentID": "doc2"}},
            ]
        )
        result = _to_mongodb_filter(where)
        assert result == {
            "$and": [
                {"workspace_id": {"$eq": "ws1"}},
                {"document_id": {"$ne": "doc1"}},
                {"document_id": {"$ne": "doc2"}},
            ]
        }


class TestIsNativeKey:
    """Tests for _is_native_key helper."""

    @pytest.mark.parametrize(
        "key",
        ["workspace_id", "document_id", "workspaceId", "workspaceID", "documentId", "documentID"],
    )
    def test_native_keys(self, key):
        assert _is_native_key(key) is True

    @pytest.mark.parametrize("key", ["docbaseName", "containerID", "tenantId", "tags", "custom_field"])
    def test_custom_metadata_keys(self, key):
        assert _is_native_key(key) is False


class TestBuildSearchFilter:
    """Tests for build_search_filter — all filters are passed through to the DB."""

    def test_column_only_passes_through(self):
        """Column-only filter is returned as full MongoDB-style filter."""
        where = _build_where_clauses([{"workspaceID": "ws1"}])
        result = build_search_filter(where)
        assert result == {"workspace_id": {"$eq": "ws1"}}

    def test_metadata_only_passes_through(self):
        """Filter with only custom metadata keys is now fully included."""
        where = _build_where_clauses([{"docbaseName": "doc1"}])
        result = build_search_filter(where)
        assert result == {"docbaseName": {"$eq": "doc1"}}

    def test_mixed_and_passes_through(self):
        """AND group with mixed keys — all conditions go to DB filter."""
        where = _build_where_clauses([{"workspaceID": "ws1", "docbaseName": "doc1"}])
        result = build_search_filter(where)
        assert result == {
            "$and": [
                {"workspace_id": {"$eq": "ws1"}},
                {"docbaseName": {"$eq": "doc1"}},
            ]
        }

    def test_mixed_or_passes_through(self):
        """OR groups with metadata keys are now fully included."""
        where = _build_where_clauses([{"workspaceID": "ws1"}, {"docbaseName": "doc1"}])
        result = build_search_filter(where)
        assert result == {
            "$or": [
                {"workspace_id": {"$eq": "ws1"}},
                {"docbaseName": {"$eq": "doc1"}},
            ]
        }


class TestBuildMetadataPostFilter:
    """Tests for build_metadata_post_filter (deprecated — always returns None)."""

    def test_none_inputs(self):
        assert build_metadata_post_filter(None, None) is None

    def test_column_only_returns_none(self):
        """No post-filter needed for pure column conditions."""
        assert build_metadata_post_filter(_build_where_clauses([{"workspaceID": "ws1"}]), None) is None

    def test_custom_metadata_returns_none(self):
        """Post-filter is deprecated; all filtering is at the DB level."""
        pf = build_metadata_post_filter(_build_where_clauses([{"docbaseName": "doc-001"}]))
        assert pf is None

    def test_mixed_state_and_query_returns_none(self):
        """Post-filter is deprecated; all filtering is at the DB level."""
        pf = build_metadata_post_filter(
            state_where=_build_where_clauses([{"workspaceID": "ws1"}]),
            query_where=_build_where_clauses([{"docbaseName": "mydb"}]),
        )
        assert pf is None

    def test_or_groups_returns_none(self):
        """Post-filter is deprecated; all filtering is at the DB level."""
        pf = build_metadata_post_filter(
            state_where=_build_where_clauses([{"workspaceID": "ws1"}, {"docbaseName": "doc1"}]),
        )
        assert pf is None

    def test_and_group_mixed_returns_none(self):
        """Post-filter is deprecated; all filtering is at the DB level."""
        pf = build_metadata_post_filter(_build_where_clauses([{"workspaceID": "ws1", "docbaseName": "doc1"}]))
        assert pf is None

    def test_neq_custom_metadata_returns_none(self):
        """Post-filter is deprecated; all filtering is at the DB level."""
        pf = build_metadata_post_filter(_build_where_clauses([{"_NEQ_": {"containerID": "excluded"}}]))
        assert pf is None


class TestOptimizeSameKeyGroups:
    """Tests for _optimize_same_key_groups helper."""

    def test_same_key_eq_merged(self):
        groups = [
            {"workspace_id": {"$eq": "ws1"}},
            {"workspace_id": {"$eq": "ws2"}},
        ]
        result = _optimize_same_key_groups(groups)
        assert result == [{"workspace_id": {"$in": ["ws1", "ws2"]}}]

    def test_same_key_eq_and_in_merged(self):
        groups = [
            {"workspace_id": {"$eq": "ws1"}},
            {"workspace_id": {"$in": ["ws2", "ws3"]}},
        ]
        result = _optimize_same_key_groups(groups)
        assert result == [{"workspace_id": {"$in": ["ws1", "ws2", "ws3"]}}]

    def test_different_keys_not_merged(self):
        groups = [
            {"workspace_id": {"$eq": "ws1"}},
            {"document_id": {"$eq": "doc1"}},
        ]
        result = _optimize_same_key_groups(groups)
        assert len(result) == 2

    def test_multi_key_group_not_merged(self):
        """Multi-key AND groups are not candidates for merging."""
        groups = [
            {"$and": [{"workspace_id": {"$eq": "ws1"}}, {"document_id": {"$eq": "doc1"}}]},
            {"workspace_id": {"$eq": "ws2"}},
        ]
        result = _optimize_same_key_groups(groups)
        assert len(result) == 2

    def test_preserves_position_order(self):
        groups = [
            {"workspace_id": {"$eq": "ws1"}},
            {"document_id": {"$eq": "doc1"}},
            {"workspace_id": {"$eq": "ws2"}},
        ]
        result = _optimize_same_key_groups(groups)
        # workspace_id merged at position 0, document_id stays at position 1
        assert result == [
            {"workspace_id": {"$in": ["ws1", "ws2"]}},
            {"document_id": {"$eq": "doc1"}},
        ]
