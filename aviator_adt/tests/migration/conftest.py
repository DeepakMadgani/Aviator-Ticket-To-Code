"""Pytest bootstrap for migration tests.

Sets minimal environment defaults so modules that instantiate
MigrationSettings at import time can be imported during collection.
"""

import os

# Required by migration.settings at import time.
os.environ.setdefault("MIGRATION_SOURCE_DSN", "postgresql://src:5432/csai")
