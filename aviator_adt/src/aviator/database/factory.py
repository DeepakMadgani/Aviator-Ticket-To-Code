"""Factory for creating database adapters.

Mirrors ``VectorStoreFactory``: decides which concrete ``DatabaseAdapter``
to instantiate based on the ``DATABASE_BACKEND`` setting (default ``postgres``).

Adding a new backend
--------------------
1. Create a new subclass of ``DatabaseAdapter`` in ``aviator.database.adapters``.
2. Add a ``case`` branch here.
3. No other file needs to change.
"""

from aviator.database.adapters.base import DatabaseAdapter
from aviator.settings import settings


class DatabaseAdapterFactory:
    """Factory that creates ``DatabaseAdapter`` instances from configuration."""

    @staticmethod
    def create_adapter() -> DatabaseAdapter:
        """Return a configured ``DatabaseAdapter`` based on settings.

        Controlled by ``settings.database_backend`` (env: ``DATABASE_BACKEND``).
        Supported values: ``"postgres"`` (default).

        Returns:
            A concrete :class:`~aviator.database.adapters.base.DatabaseAdapter`.

        Raises:
            ValueError: For unsupported backend values.

        """
        backend = settings.database_backend

        match backend:
            case "postgres":
                from aviator.database.adapters.postgres import PostgresDatabaseAdapter

                return PostgresDatabaseAdapter()

            case _:
                msg = f"Unsupported database backend: '{backend}'."
                raise ValueError(msg)
