"""Unit tests for PostgresAdapter class."""

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

# Set test environment before importing modules
os.environ["VECTOR_STORE"] = "memory"
os.environ["CHECKPOINTER"] = "memory"


class TestPostgresAdapter:
    """Tests for PostgresAdapter class."""

    @pytest.fixture
    def mock_engine(self):
        """Create a mock SQLAlchemy engine."""
        with patch("aviator.database.postgres_adapter.create_engine") as mock_create:
            mock_engine_instance = MagicMock()
            mock_create.return_value = mock_engine_instance
            yield mock_engine_instance

    @pytest.fixture
    def mock_sessionmaker(self):
        """Create a mock sessionmaker."""
        with patch("aviator.database.postgres_adapter.sessionmaker") as mock_maker:
            mock_session_factory = MagicMock()
            mock_maker.return_value = mock_session_factory
            yield mock_session_factory

    def test_initialization(self):
        """Test PostgresAdapter initialization."""
        from aviator.database.postgres_adapter import PostgresAdapter

        connection_string = "postgresql://user:pass@localhost/testdb"
        adapter = PostgresAdapter(connection_string)

        assert adapter.connection_string == connection_string
        assert adapter._engine is None
        assert adapter._SessionLocal is None

    def test_setup_creates_engine_and_tables(self, mock_engine, mock_sessionmaker):
        """Test that setup creates engine and tables."""
        from aviator.database.postgres_adapter import PostgresAdapter

        with patch("aviator.database.postgres_adapter.Base") as mock_base:
            adapter = PostgresAdapter("postgresql://localhost/test")
            adapter.setup()

            # Verify engine was created
            assert adapter._engine is mock_engine

            # Verify tables were created via create_tables() which uses engine.begin()
            # The actual call is create_all(bind=conn) where conn is a schema-translated connection
            assert mock_base.metadata.create_all.call_count == 1
            call_kwargs = mock_base.metadata.create_all.call_args
            assert call_kwargs.kwargs.get("bind") is not None or call_kwargs.args

            # Verify session factory was created
            assert adapter._SessionLocal is mock_sessionmaker

    def test_setup_failure_raises_exception(self):
        """Test that setup raises exception on database connection failure."""
        from aviator.database.postgres_adapter import PostgresAdapter

        with patch(
            "aviator.database.postgres_adapter.create_engine",
            side_effect=OperationalError("Connection failed", None, None),
        ):
            adapter = PostgresAdapter("postgresql://localhost/test")

            with pytest.raises(OperationalError):
                adapter.setup()

    def test_get_session_without_setup_raises_error(self):
        """Test that get_session raises RuntimeError if setup not called."""
        from aviator.database.postgres_adapter import PostgresAdapter

        adapter = PostgresAdapter("postgresql://localhost/test")

        with pytest.raises(RuntimeError, match="Database not initialized"):
            adapter.get_session()

    def test_get_session_creates_multiple_sessions(self, mock_engine, mock_sessionmaker):
        """Test that get_session can create multiple independent sessions."""
        from aviator.database.postgres_adapter import PostgresAdapter

        with patch("aviator.database.postgres_adapter.Base"):
            adapter = PostgresAdapter("postgresql://localhost/test")
            adapter.setup()

            mock_session1 = MagicMock()
            mock_session2 = MagicMock()
            mock_sessionmaker.side_effect = [mock_session1, mock_session2]

            session1 = adapter.get_session()
            session2 = adapter.get_session()

            # Sessions should be different instances
            assert session1 is not session2
            assert mock_sessionmaker.call_count == 2

    def test_close_disposes_engine(self, mock_engine):
        """Test that close disposes the engine."""
        from aviator.database.postgres_adapter import PostgresAdapter

        with (
            patch("aviator.database.postgres_adapter.Base"),
            patch("aviator.database.postgres_adapter.sessionmaker"),
        ):
            adapter = PostgresAdapter("postgresql://localhost/test")
            adapter.setup()

            adapter.close()

            # Verify engine was disposed
            mock_engine.dispose.assert_called_once()

    def test_close_without_setup(self):
        """Test that close works even if setup was never called."""
        from aviator.database.postgres_adapter import PostgresAdapter

        adapter = PostgresAdapter("postgresql://localhost/test")

        # Should not raise any errors
        adapter.close()

    def test_close_without_engine(self, mock_sessionmaker):
        """Test that close handles case where engine is None."""
        from aviator.database.postgres_adapter import PostgresAdapter

        adapter = PostgresAdapter("postgresql://localhost/test")
        adapter._SessionLocal = mock_sessionmaker
        adapter._engine = None

        # Should not raise any errors
        adapter.close()

    def test_create_tables_without_setup_raises_error(self):
        """Test that create_tables raises RuntimeError if engine not initialized."""
        from aviator.database.postgres_adapter import PostgresAdapter

        adapter = PostgresAdapter("postgresql://localhost/test")

        with pytest.raises(RuntimeError, match="Database not initialized"):
            adapter.create_tables()

    def test_create_tables_with_schema_name(self, mock_engine):
        """Test create_tables creates schema and calls create_all for named schema."""
        from aviator.database.postgres_adapter import PostgresAdapter

        with patch("aviator.database.postgres_adapter.Base") as mock_base:
            adapter = PostgresAdapter("postgresql://localhost/test")
            adapter._engine = mock_engine
            mock_conn = MagicMock()
            mock_schema_conn = MagicMock()
            mock_engine.begin.return_value.__enter__.return_value = mock_conn
            mock_engine.begin.return_value.__exit__.return_value = False
            mock_conn.execution_options.return_value = mock_schema_conn

            adapter.create_tables(schema_name="tenant_abc")

            mock_conn.execute.assert_called_once()
            executed_sql = mock_conn.execute.call_args.args[0]
            assert 'CREATE SCHEMA IF NOT EXISTS "tenant_abc"' in str(executed_sql)
            mock_conn.execution_options.assert_called_once_with(schema_translate_map={None: "tenant_abc"})
            mock_base.metadata.create_all.assert_called_once_with(bind=mock_schema_conn)

    def test_create_tables_without_schema_name(self, mock_engine):
        """Test create_tables without schema does not issue CREATE SCHEMA."""
        from aviator.database.postgres_adapter import PostgresAdapter

        with patch("aviator.database.postgres_adapter.Base") as mock_base:
            adapter = PostgresAdapter("postgresql://localhost/test")
            adapter._engine = mock_engine
            mock_conn = MagicMock()
            mock_engine.begin.return_value.__enter__.return_value = mock_conn
            mock_engine.begin.return_value.__exit__.return_value = False

            adapter.create_tables()

            mock_conn.execute.assert_not_called()
            mock_conn.execution_options.assert_not_called()
            mock_base.metadata.create_all.assert_called_once_with(bind=mock_conn)

    def test_get_session_with_schema_name(self, mock_engine, mock_sessionmaker):
        """Test that get_session with schema_name creates a schema-translated session."""
        from aviator.database.postgres_adapter import PostgresAdapter

        with patch("aviator.database.postgres_adapter.Base"):
            adapter = PostgresAdapter("postgresql://localhost/test")
            adapter.setup()

            mock_schema_engine = MagicMock()
            mock_engine.execution_options.return_value = mock_schema_engine

            schema_session = adapter.get_session(schema_name="tenant_xyz")

            # Should have called execution_options with schema_translate_map
            mock_engine.execution_options.assert_called_once_with(schema_translate_map={None: "tenant_xyz"})
            assert schema_session is not None

    def test_get_session_without_schema_name_uses_default(self, mock_engine, mock_sessionmaker):
        """Test that get_session without schema_name returns default session."""
        from aviator.database.postgres_adapter import PostgresAdapter

        with patch("aviator.database.postgres_adapter.Base"):
            adapter = PostgresAdapter("postgresql://localhost/test")
            adapter.setup()

            session = adapter.get_session()

            # Should NOT call execution_options for schema routing
            mock_engine.execution_options.assert_not_called()
            assert session is not None
