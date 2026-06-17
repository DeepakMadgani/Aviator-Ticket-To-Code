# Aviator Platform — Repository Intelligence Core (Java)

AST-first, patch-first foundation for the autonomous engineering platform.
**Phase 1**: index a Java repository into a queryable structural graph.

This phase is Python-only (no JVM required) and uses `tree-sitter-java` for
parsing. Later phases will add hybrid retrieval, LangGraph workflow, patch
generation and Playwright validation — all reusing this index.

## What it does today

Given a Java repo, the indexer extracts:

- packages, imports
- classes, interfaces, enums, records, annotation types (including nested)
- methods, constructors, fields, parameters
- modifiers + annotations
- inheritance edges (`extends`, `implements`)
- best-effort call edges from method bodies

Output is persisted to a single SQLite file (`<repo>/.aviator/index.db`) with
an FTS5 virtual table over symbol names — ready for keyword + symbol search.

## Install

```powershell
# From the workspace root, with the aviator-plugin-sample venv active:
cd aviator-platform
pip install -e .
```

Python 3.11+ required. The only mandatory deps are `tree-sitter`,
`tree-sitter-java`, `pydantic`, `typer`, `rich`.

## Quick start

```powershell
# 1. Build the index for a Java repo
aviator index C:\path\to\your-java-project

# 2. Inspect counts
aviator stats --db C:\path\to\your-java-project\.aviator\index.db

# 3. Search for symbols
aviator search "UserService" --kind class

# 4. Show a symbol + its outgoing edges
aviator show <symbol_id>

# 5. Find call sites of a method
aviator callers saveUser
```

## Optional backends

SQLite is the default and is enough for the next several phases. Neo4j and
Qdrant are wired up for later (graph traversal at scale, semantic search):

```powershell
docker compose -f docker-compose.yml up -d neo4j qdrant
pip install -e .[neo4j,qdrant]
```

## Project layout

```
aviator-platform/
├── pyproject.toml
├── docker-compose.yml
├── aviator_core/
│   ├── models.py            # Pydantic: Symbol, Edge, FileRecord
│   ├── parsers/
│   │   └── java_parser.py   # tree-sitter-java
│   ├── storage/
│   │   └── sqlite_store.py  # SQLite + FTS5
│   ├── indexer.py           # repo → store orchestrator
│   ├── query.py             # search / neighbors / callers
│   └── cli.py               # `aviator` CLI
└── tests/
    └── test_java_parser.py
```

## Roadmap (per master architecture)

1. **Now** — Repository Intelligence Core (this README).
2. Hybrid retrieval (keyword + symbol + AST + semantic + graph).
3. LangGraph multi-stage localization workflow.
4. Patch-first modification (text-range diffs via tree-sitter ranges).
5. Playwright runtime validation harness.
6. Transparent execution UI (Next.js) wired to LangGraph nodes.
7. Incremental indexing via `git diff`.
