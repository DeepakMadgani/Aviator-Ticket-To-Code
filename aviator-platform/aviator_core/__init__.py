"""Aviator Platform — Repository Intelligence Core.

Public API surface:

- :mod:`aviator_core.models`    — Pydantic data models for symbols, edges, files.
- :mod:`aviator_core.parsers`   — Language parsers (currently: Java via tree-sitter).
- :mod:`aviator_core.storage`   — Persistent stores (SQLite, optional Neo4j).
- :mod:`aviator_core.indexer`   — Orchestrates parse → store.
- :mod:`aviator_core.query`     — Symbol / dependency queries.
- :mod:`aviator_core.cli`       — `aviator` CLI entry point.
"""

__version__ = "0.1.0"
