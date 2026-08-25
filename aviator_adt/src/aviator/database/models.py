"""Database models for workspace documents and summaries."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base

from aviator.settings import settings

Base = declarative_base()


class DocumentSummary(Base):
    """Document summary model."""

    __tablename__ = settings.summary_table_name

    document_id = Column(String, primary_key=True)
    summary = Column(Text, nullable=False)
    title = Column(String, nullable=False)
    title_embeddings = Column(Vector(settings.vector_size), nullable=True)


class MCPServerConfiguration(Base):
    """Server configuration model."""

    __tablename__ = "mcp_server_configurations"

    server_name = Column(String, nullable=False)
    server_id = Column(String, primary_key=True)
    server_config = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)


class TenantLookupConfig(Base):
    """Tenant lookup configuration model."""

    __tablename__ = "tenant_lookup_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(Text, nullable=False)
    value = Column(Text, nullable=False)
    subscription_id = Column(Text, nullable=True, unique=True)


class A2ATask(Base):
    """Persists A2A task lifecycle state across requests.

    One row per ``message/send`` or ``message/stream`` invocation.
    The background runner updates *state* and *artifacts* as the LangGraph
    graph progresses.  Tenant isolation is achieved by routing sessions to
    the appropriate schema via ``schema_translate_map``.
    """

    __tablename__ = "a2a_tasks"

    id = Column(String, primary_key=True)
    context_id = Column(String, nullable=True)
    state = Column(String, nullable=False, default="submitted")
    artifacts = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
