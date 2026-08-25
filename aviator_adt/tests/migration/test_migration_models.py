"""Tests for Pydantic migration models."""

import pytest

from migration.models import MigrationBatch, MigrationProgress, SourceRow


class TestSourceRow:
    """Tests for SourceRow model."""

    def test_minimal(self) -> None:
        row = SourceRow(id="abc-123", embedding=[0.1, 0.2])
        assert row.id == "abc-123"
        assert row.text is None
        assert row.metadata == {}
        assert row.embedding == [0.1, 0.2]

    def test_full(self) -> None:
        row = SourceRow(
            id="abc-123",
            text="Hello",
            metadata={"workspaceID": "ws1"},
            embedding=[0.1, 0.2, 0.3],
        )
        assert row.text == "Hello"
        assert row.metadata["workspaceID"] == "ws1"


class TestMigrationBatch:
    """Tests for MigrationBatch model with target_schema and ID list."""

    def test_round_trip(self) -> None:
        batch = MigrationBatch(target_schema="tenant_acme", ids=["id-1", "id-2", "id-3"])
        d = batch.model_dump(mode="json")
        restored = MigrationBatch.model_validate(d)
        assert restored.target_schema == "tenant_acme"
        assert restored.ids == ["id-1", "id-2", "id-3"]

    def test_ids_must_not_be_empty(self) -> None:
        with pytest.raises(ValueError):
            MigrationBatch(target_schema="public", ids=[])

    def test_single_id(self) -> None:
        batch = MigrationBatch(target_schema="public", ids=["single-id"])
        assert len(batch.ids) == 1
        assert batch.target_schema == "public"

    def test_large_batch(self) -> None:
        ids = [f"id-{i}" for i in range(5000)]
        batch = MigrationBatch(target_schema="tenant_big", ids=ids)
        assert len(batch.ids) == 5000
        assert batch.target_schema == "tenant_big"

    def test_target_schema_required(self) -> None:
        with pytest.raises(ValueError):
            MigrationBatch(ids=["id-1"])  # type: ignore[call-arg]


class TestMigrationProgress:
    """Tests for MigrationProgress model."""

    def test_defaults(self) -> None:
        p = MigrationProgress()
        assert p.last_id is None
        assert p.status == "in_progress"
        assert p.total_rows is None
        assert p.migrated == 0

    def test_custom(self) -> None:
        p = MigrationProgress(last_id="abc", status="completed", total_rows=1000, migrated=1000)
        assert p.status == "completed"
        assert p.migrated == 1000
