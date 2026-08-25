"""Tests for where filter backward compatibility and model validation.

Verifies that API models correctly accept the new dict-based where clause format
including custom metadata keys, NEQ filters, and $contains operator.
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from aviator.models import (
    ChatRequestModel,
    ContextRequestModel,
    StateModel,
    WhereClauseReferenceModel,
)
from aviator.utils.reducers import reduce_where_clauses


def _build_where_clauses(clauses: list[dict]) -> list[WhereClauseReferenceModel]:
    """Build where clauses for tests."""
    return [WhereClauseReferenceModel(**clause) for clause in clauses if clause is not None]


class TestStateModelWhereClause:
    """Tests for StateModel where clause acceptance."""

    @pytest.mark.parametrize(
        "where",
        [
            [],
            [{"workspaceID": "ws-1"}],
            [{"documentID": "doc-1"}],
            [{"docbaseName": "myDocbase"}],
            [{"_NEQ_": {"documentID": "doc-3"}}],
            [{"workspaceID": "ws-1"}, {"workspaceID": "ws-2"}, {"_NEQ_": {"documentID": "doc-3"}}],
            [{"workspaceID": "ws-1", "tenantId": "tenantA"}],
            [{"tags": {"$contains": "tag1"}}, {"workspaceID": {"$contains": "ws1"}}],
        ],
        ids=[
            "empty",
            "workspace",
            "document",
            "custom-key",
            "neq",
            "complex-mixed",
            "and-semantics",
            "contains-operator",
        ],
    )
    def test_accepts_where_clause(self, where):
        where_clauses = _build_where_clauses(where)
        state = StateModel(messages=[], where=where_clauses)
        assert state.where == where_clauses

    def test_default_where_is_empty(self):
        state = StateModel(messages=[])
        assert state.where == []


class TestStateModelHelpers:
    """Tests for StateModel get_workspace_ids and get_document_ids helpers."""

    def test_get_workspace_ids_simple(self):
        state = StateModel(messages=[], where=_build_where_clauses([{"workspaceID": "ws-1"}]))
        assert state.get_workspace_ids() == ["ws-1"]

    def test_get_workspace_ids_multiple(self):
        state = StateModel(messages=[], where=_build_where_clauses([{"workspaceID": "ws-1"}, {"workspaceID": "ws-2"}]))
        ids = state.get_workspace_ids()
        assert ids is not None
        assert set(ids) == {"ws-1", "ws-2"}

    def test_get_workspace_ids_none_when_empty(self):
        state = StateModel(messages=[], where=_build_where_clauses([]))
        assert state.get_workspace_ids() is None

    def test_get_workspace_ids_ignores_custom_keys(self):
        state = StateModel(messages=[], where=_build_where_clauses([{"docbaseName": "doc1"}]))
        assert state.get_workspace_ids() is None

    def test_get_document_ids_simple(self):
        state = StateModel(messages=[], where=_build_where_clauses([{"documentID": "doc-1"}]))
        assert state.get_document_ids() == ["doc-1"]

    def test_get_document_ids_multiple(self):
        state = StateModel(messages=[], where=_build_where_clauses([{"documentID": "doc-1"}, {"documentID": "doc-2"}]))
        ids = state.get_document_ids()
        assert ids is not None
        assert set(ids) == {"doc-1", "doc-2"}

    def test_get_document_ids_none_when_empty(self):
        state = StateModel(messages=[], where=_build_where_clauses([]))
        assert state.get_document_ids() is None


class TestChatRequestModelWhereClause:
    """Tests for ChatRequestModel where clause acceptance."""

    def test_default_where(self):
        req = ChatRequestModel(
            messages=[{"author": "user", "content": "hello"}],
        )
        assert req.where == []

    def test_complex_mixed_filter(self):
        """Test the full Sample 5 from the ticket."""
        req = ChatRequestModel(
            messages=[{"author": "user", "content": "hello"}],
            where=_build_where_clauses(
                [
                    {"workspaceID": "ws1"},
                    {"workspaceID": "ws2"},
                    {"_NEQ_": {"documentID": "doc3"}},
                ]
            ),
        )
        assert len(req.where) == 3


class TestContextRequestModelMetadata:
    """Tests for ContextRequestModel metadata (where clause) acceptance."""

    def test_default_metadata(self):
        req = ContextRequestModel(query="test")
        assert req.metadata == []


class TestWhereReducer:
    """Tests for custom where reducer behavior."""

    def test_reduce_where_overrides_by_default(self):
        existing = _build_where_clauses([{"workspaceID": "ws-1"}])
        incoming = _build_where_clauses([{"workspaceID": "ws-2"}])

        result = reduce_where_clauses(existing, incoming)

        assert result == incoming

    def test_reduce_where_appends_when_flag_enabled(self):
        existing = _build_where_clauses([{"workspaceID": "ws-1"}])
        incoming = _build_where_clauses([{"workspaceID": "ws-2"}])

        result = reduce_where_clauses(existing, {"append": True, "where": incoming})

        assert result == [*existing, *incoming]


class TestWhereReducerWithServerCommand:
    """Tests reducer behavior when server-side nodes return Command(update=...)."""

    @pytest.mark.asyncio
    async def test_command_update_appends_where_in_same_thread(self):
        """A tool/agent Command(update={where: {append: true, where: [...]}}) should append state where."""

        async def command_node(state: StateModel):
            if state.extensions.get("append_where"):
                return Command(
                    update={
                        "where": {
                            "append": True,
                            "where": _build_where_clauses([{"workspaceID": "ws-2"}]),
                        }
                    }
                )
            return {}

        builder = StateGraph(StateModel)
        builder.add_node("command_node", command_node)
        builder.add_edge(START, "command_node")
        builder.add_edge("command_node", END)

        graph = builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "where-reducer-command-test"}}

        first = await graph.ainvoke(
            {
                "messages": [],
                "where": _build_where_clauses([{"workspaceID": "ws-1"}]),
                "extensions": {"append_where": False},
            },
            config=config,
        )
        assert first["where"] == _build_where_clauses([{"workspaceID": "ws-1"}])

        second = await graph.ainvoke(
            {
                "messages": [],
                "extensions": {"append_where": True},
            },
            config=config,
        )

        assert second["where"] == _build_where_clauses([{"workspaceID": "ws-1"}, {"workspaceID": "ws-2"}])

    @pytest.mark.asyncio
    async def test_command_update_without_append_overrides_where_in_same_thread(self):
        """A tool/agent Command(update={where: [...]}) should override state where."""

        async def command_node(state: StateModel):
            if state.extensions.get("override_where"):
                return Command(update={"where": _build_where_clauses([{"workspaceID": "ws-2"}])})
            return {}

        builder = StateGraph(StateModel)
        builder.add_node("command_node", command_node)
        builder.add_edge(START, "command_node")
        builder.add_edge("command_node", END)

        graph = builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "where-reducer-command-override-test"}}

        first = await graph.ainvoke(
            {
                "messages": [],
                "where": _build_where_clauses([{"workspaceID": "ws-1"}]),
                "extensions": {"override_where": False},
            },
            config=config,
        )
        assert first["where"] == _build_where_clauses([{"workspaceID": "ws-1"}])

        second = await graph.ainvoke(
            {
                "messages": [],
                "extensions": {"override_where": True},
            },
            config=config,
        )

        assert second["where"] == _build_where_clauses([{"workspaceID": "ws-2"}])
