"""Unit tests for DatabaseManager and database setup functionality."""

import os
from unittest.mock import MagicMock, patch

import pytest

from aviator.database.adapters.base import DatabaseAdapter

# Set test environment before importing modules
os.environ["VECTOR_STORE"] = "memory"
os.environ["CHECKPOINTER"] = "memory"


class TestDatabaseManager:
    """Tests for DatabaseManager class."""

    @pytest.fixture
    def reset_database_manager(self):
        """Reset the database manager singleton before each test."""
        from aviator.database import database_manager

        database_manager.reset()
        yield database_manager
        database_manager.reset()

    def test_setup_database_initializes_adapter(self, reset_database_manager):
        """Test that setup_database initializes an adapter via the factory."""
        with patch("aviator.database.DatabaseAdapterFactory.create_adapter") as mock_create_adapter:
            mock_adapter = MagicMock(spec=DatabaseAdapter)
            mock_create_adapter.return_value = mock_adapter

            reset_database_manager.setup_database()

            # Verify adapter was created and setup was called
            mock_create_adapter.assert_called_once_with()
            mock_adapter.setup.assert_called_once()

    def test_setup_database_called_multiple_times(self, reset_database_manager):
        """Test that setup_database can be called multiple times safely."""
        with patch("aviator.database.DatabaseAdapterFactory.create_adapter") as mock_create_adapter:
            mock_adapter = MagicMock(spec=DatabaseAdapter)
            mock_create_adapter.return_value = mock_adapter

            # Call setup multiple times
            reset_database_manager.setup_database()
            reset_database_manager.setup_database()
            reset_database_manager.setup_database()

            # Adapter should only be initialized once
            assert mock_create_adapter.call_count == 1
            assert mock_adapter.setup.call_count == 1

    def test_get_adapter_without_setup(self, reset_database_manager):
        """Test that get() creates an adapter via the factory if needed."""
        with patch("aviator.database.DatabaseAdapterFactory.create_adapter") as mock_create_adapter:
            mock_adapter = MagicMock(spec=DatabaseAdapter)
            mock_create_adapter.return_value = mock_adapter

            # Call get without setup
            adapter = reset_database_manager.get()

            # Verify adapter was created and setup was called
            assert adapter is mock_adapter
            mock_create_adapter.assert_called_once_with()
            mock_adapter.setup.assert_called_once()

    def test_get_adapter_after_setup(self, reset_database_manager):
        """Test that get() returns existing adapter after setup."""
        with patch("aviator.database.DatabaseAdapterFactory.create_adapter") as mock_create_adapter:
            mock_adapter = MagicMock(spec=DatabaseAdapter)
            mock_create_adapter.return_value = mock_adapter

            reset_database_manager.setup_database()
            adapter1 = reset_database_manager.get()
            adapter2 = reset_database_manager.get()

            # Both should return the same adapter instance
            assert adapter1 is adapter2
            assert adapter1 is mock_adapter
            # Setup should only be called once
            assert mock_adapter.setup.call_count == 1

    def test_reset_closes_adapter(self, reset_database_manager):
        """Test that reset() closes the adapter."""
        with patch("aviator.database.DatabaseAdapterFactory.create_adapter") as mock_create_adapter:
            mock_adapter = MagicMock(spec=DatabaseAdapter)
            mock_create_adapter.return_value = mock_adapter

            reset_database_manager.setup_database()
            reset_database_manager.reset()

            # Verify close was called
            mock_adapter.close.assert_called_once()

    def test_reset_without_setup(self, reset_database_manager):
        """Test that reset() works even if setup was never called."""
        # Should not raise any errors
        reset_database_manager.reset()


class TestDatabaseSetupInStartup:
    """Tests for database setup during application startup."""

    def test_database_setup_called_with_postgres_checkpointer(self):
        """Test that database setup is called when checkpointer is postgres."""
        from aviator.database import database_manager

        with (
            patch("aviator.settings.settings.checkpointer", "postgres"),
            patch.object(database_manager, "setup_database") as mock_setup,
            patch("aviator.vector_store.vector_store.setup_vector_store"),
        ):
            # Import main.py to trigger lifespan context
            # Manually trigger lifespan startup
            import asyncio

            from aviator.main import lifespan

            async def run_lifespan():
                async with lifespan(None):
                    pass

            asyncio.run(run_lifespan())

            # Verify database setup was called
            mock_setup.assert_called_once()

    def test_database_setup_skipped_with_memory_checkpointer(self):
        """Test that database setup is skipped when checkpointer is memory."""
        from aviator.database import database_manager

        with (
            patch("aviator.settings.settings.checkpointer", "memory"),
            patch.object(database_manager, "setup_database") as mock_setup,
            patch("aviator.vector_store.vector_store.setup_vector_store"),
        ):
            # Import main.py to trigger lifespan context
            # Manually trigger lifespan startup
            import asyncio

            from aviator.main import lifespan

            async def run_lifespan():
                async with lifespan(None):
                    pass

            asyncio.run(run_lifespan())

            # Verify database setup was NOT called
            mock_setup.assert_not_called()
