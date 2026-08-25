"""Pydantic models for migration batch messages."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceRow(BaseModel):
    """A single row from the CSAI source table.

    Used internally by the consumer after fetching rows from the source DB.
    Not serialised in queue messages (the ID-based approach sends only IDs,
    and the worker fetches rows directly using WHERE id IN (...)).
    """

    id: str = Field(..., description="Source UUID primary key.")
    text: str | None = Field(default=None, description="Document text content.")
    metadata: dict = Field(default_factory=dict, description="JSONB metadata.")
    embedding: list[float] = Field(..., description="Vector embedding.")


class MigrationBatch(BaseModel):
    """A batch of source row IDs for a single tenant schema.

    The producer reads IDs and tenant info from the source table, groups by tenant,
    then batches IDs within each tenant and publishes.
    Workers receive a single-tenant batch, fetch rows using WHERE id IN (...),
    and write directly to the specified target schema (no grouping needed).
    """

    target_schema: str = Field(..., description="Target PostgreSQL schema name.")
    ids: list[str] = Field(..., min_length=1, description="List of source row UUIDs to migrate.")


class MigrationProgress(BaseModel):
    """Snapshot of overall migration progress."""

    last_id: str | None = Field(default=None, description="Last migrated source row UUID.")
    status: str = Field(default="in_progress", description="'in_progress' or 'completed'.")
    total_rows: int | None = Field(default=None, description="Total source row count.")
    migrated: int = Field(default=0, description="Rows migrated so far.")


# ── Summary backfill models ──────────────────────────────────────────


class SummarySourceDocument(BaseModel):
    """A single document with pre-aggregated text from the CSAI source DB."""

    document_id: str = Field(..., description="Source documentID from metadata.")
    workspace_id: str = Field(..., description="Source workspaceID from metadata.")
    aggregated_text: str = Field(..., description="All chunks concatenated in order.")


class SummaryBatch(BaseModel):
    """A batch of documents destined for summary generation in a single target schema."""

    target_schema: str = Field(..., description="Target PostgreSQL schema name.")
    documents: list[SummarySourceDocument] = Field(..., description="Documents in this batch.")
