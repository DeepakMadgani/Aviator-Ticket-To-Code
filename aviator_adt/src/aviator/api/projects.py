"""Project management API for PHASE 1 testing."""

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

# Persistent project storage backed by a local SQLite registry
_REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / ".aviator_projects.db"


def _get_registry() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_REGISTRY_PATH))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'created',
            indexed INTEGER NOT NULL DEFAULT 0
        )"""
    )
    conn.commit()
    return conn


class Project(BaseModel):
    """Project model."""
    id: str
    name: str
    type: str
    path: str
    status: str = "created"
    indexed: bool = False


def _load_project(project_id: str) -> Optional[Project]:
    with _get_registry() as conn:
        row = conn.execute("SELECT id, name, type, path, status, indexed FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        return None
    return Project(id=row[0], name=row[1], type=row[2], path=row[3], status=row[4], indexed=bool(row[5]))


def _save_project(project: Project) -> None:
    with _get_registry() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO projects (id, name, type, path, status, indexed) VALUES (?,?,?,?,?,?)",
            (project.id, project.name, project.type, project.path, project.status, int(project.indexed))
        )
        conn.commit()


class CreateProjectRequest(BaseModel):
    name: str
    type: str
    path: str
    technology: Optional[str] = None

@router.post("")
async def create_project(request: CreateProjectRequest):
    """Create a new project."""
    project = Project(
        id=str(uuid.uuid4()),
        name=request.name,
        type=request.type,
        path=request.path,
        status="created",
        indexed=False,
    )
    _save_project(project)
    logger.info(f"Created project {request.name} at {request.path}")
    return project


@router.get("")
async def list_projects():
    """List all projects."""
    with _get_registry() as conn:
        rows = conn.execute("SELECT id, name, type, path, status, indexed FROM projects").fetchall()
    projects = [Project(id=r[0], name=r[1], type=r[2], path=r[3], status=r[4], indexed=bool(r[5])) for r in rows]
    return {"projects": projects}


def _run_indexing_with_result(project_id: str) -> dict:
    """Run full indexing synchronously in a thread. Returns full result dict."""
    import sys
    platform_path = Path(__file__).parent.parent.parent.parent.parent / "aviator-platform"
    if str(platform_path) not in sys.path:
        sys.path.insert(0, str(platform_path))

    from aviator_core.indexer import index_repository
    from aviator_core.models import Edge, EdgeKind, SourceLocation, Symbol, SymbolKind
    from aviator_core.storage.neo4j_store import Neo4jStore
    from aviator_core.storage.sqlite_store import SqliteStore

    project = _load_project(project_id)
    if not project:
        return {"status": "error", "detail": "Project not found"}

    try:
        logger.info(f"🔍 Starting indexing for {project.name}...")

        index_path = Path(project.path) / ".aviator" / "index.db"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        sqlite_store = SqliteStore(index_path)

        stats = index_repository(Path(project.path), sqlite_store)
        logger.info(f"✅ SQLite indexed {project.name}: {stats.files_scanned} files, {stats.symbols} symbols, {stats.edges} edges")

        if os.getenv("NEO4J_PASSWORD"):
            logger.info("🌐 Indexing to Neo4j...")
            neo4j_store = Neo4jStore()

            cursor = sqlite_store._conn.execute(
                "SELECT id, kind, name, qualified_name, package, parent_id, "
                "path, start_line, end_line, start_col, end_col, "
                "signature, return_type, modifiers, annotations, parameter_types, "
                "spring_stereotype, spring_endpoints, spring_dependencies, "
                "is_feign_client, feign_service_name FROM symbols"
            )
            symbols: list[Symbol] = []
            for row in cursor.fetchall():
                try:
                    symbols.append(Symbol(
                        id=row[0], kind=SymbolKind(row[1]), name=row[2],
                        qualified_name=row[3], package=row[4], parent_id=row[5],
                        location=SourceLocation(
                            path=row[6], start_line=row[7] or 1, end_line=row[8] or 1,
                            start_col=row[9] or 1, end_col=row[10] or 1,
                        ),
                        signature=row[11], return_type=row[12],
                        modifiers=json.loads(row[13]) if row[13] else [],
                        annotations=json.loads(row[14]) if row[14] else [],
                        parameter_types=json.loads(row[15]) if row[15] else [],
                        spring_stereotype=row[16],
                        spring_endpoints=json.loads(row[17]) if row[17] else [],
                        spring_dependencies=json.loads(row[18]) if row[18] else [],
                        is_feign_client=bool(row[19]) if row[19] is not None else False,
                        feign_service_name=row[20],
                    ))
                except Exception as exc:
                    logger.warning(f"   Skipping symbol '{row[2]}': {exc}")

            neo4j_store.insert_symbols(symbols)
            logger.info(f"   [SUCCESS] Inserted {len(symbols)} symbols to Neo4j")

            cursor = sqlite_store._conn.execute(
                "SELECT kind, src_id, dst_id, dst_name, path, start_line FROM edges"
            )
            edges: list[Edge] = []
            for row in cursor.fetchall():
                try:
                    edges.append(Edge(
                        kind=EdgeKind(row[0]), src_id=row[1],
                        dst_id=row[2] if row[2] else None, dst_name=row[3],
                        location=SourceLocation(path=row[4], start_line=row[5] or 1,
                            end_line=row[5] or 1) if row[4] else None,
                    ))
                except Exception as exc:
                    logger.warning(f"   Skipping edge: {exc}")

            neo4j_store.insert_edges(edges)
            logger.info(f"   [SUCCESS] Inserted {len(edges)} edges to Neo4j")
            neo4j_store.close()
            logger.info("[SUCCESS] Neo4j indexing complete")
        else:
            logger.warning("[WARNING] NEO4J_PASSWORD not set - skipping Neo4j indexing")

        sqlite_store.close()
        project.status = "indexed"
        project.indexed = True
        _save_project(project)
        logger.info(f"[SUCCESS] Project '{project.name}' fully indexed.")

        return {
            "status": "success",
            "files_scanned": stats.files_scanned,
            "symbols": stats.symbols,
            "edges": stats.edges,
        }

    except Exception as exc:
        logger.error(f"Indexing failed for '{project.name}': {exc}", exc_info=True)
        project.status = "error"
        _save_project(project)
        return {"status": "error", "detail": str(exc)}


@router.post("/{project_id}/index")
async def index_project(project_id: str):
    """Index a project fully. Waits for completion and returns the full result."""
    project = _load_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status == "indexing":
        return {"status": "already_indexing", "message": "Indexing is already in progress."}

    project.status = "indexing"
    _save_project(project)

    # Run the heavy indexing in a thread so the asyncio event loop stays unblocked,
    # but we still await it here — the caller waits for the full result.
    result = await asyncio.to_thread(_run_indexing_with_result, project_id)
    return result


@router.get("/{project_id}")
async def get_project(project_id: str):
    """Get a project by ID — use this to poll indexing status."""
    project = _load_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """Delete a project."""
    project = _load_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    with _get_registry() as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        conn.commit()
    return {"status": "deleted", "id": project_id}
