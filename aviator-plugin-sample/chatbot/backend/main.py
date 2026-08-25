"""
Aviator Chatbot - Backend API
FastAPI server with WebSocket support for real-time workflow updates
"""
from dotenv import load_dotenv
import os
from pathlib import Path

env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from pathlib import Path
from datetime import datetime
import json
import asyncio
import logging
from enum import Enum
import sys
import os
import uuid
from git import Repo

import history_store

# Fix Windows charmap encoding crash for emojis
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# SET GOOGLE CREDENTIALS BEFORE IMPORTING ticket_to_code!
if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = r'C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json'

# Add ticket_to_code src to path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

# Add aviator-platform and aviator_adt to path for ticket_to_code imports
aviator_platform_path = Path(__file__).parent.parent.parent.parent / "aviator-platform"
if str(aviator_platform_path) not in sys.path:
    sys.path.insert(0, str(aviator_platform_path))

aviator_adt_path = Path(__file__).parent.parent.parent.parent / "aviator_adt" / "src"
if str(aviator_adt_path) not in sys.path:
    sys.path.insert(0, str(aviator_adt_path))

# NOW import the existing working workflow
from ticket_to_code import run_autonomous_workflow, ValueEdgeTicket, TicketPriority
from ticket_to_code.api.routes import router as ticket_to_code_router

# Configure logging
import sys
if sys.platform == "win32":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(levelname)s:%(name)s:%(message)s')
else:
    logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Aviator Chatbot API", version="1.0.0")
app.include_router(ticket_to_code_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# DATA MODELS
# ============================================================================

class ProjectType(str, Enum):
    LOCAL = "local"
    GIT = "git"


class ExecutionMode(str, Enum):
    PIPELINE = "pipeline"
    REASONING = "reasoning"

class ProjectStatus(str, Enum):
    PENDING = "pending"
    CLONING = "cloning"
    INDEXING = "indexing"
    INDEXED = "indexed"
    READY = "ready"
    ERROR = "error"

class Project(BaseModel):
    id: str = Field(..., description="Unique project ID")
    name: str = Field(..., description="Project name")
    type: ProjectType = Field(..., description="local or git")
    path: str = Field(..., description="Local path or git URL")
    local_path: Optional[str] = Field(None, description="Local clone path for git projects")
    status: ProjectStatus = Field(default=ProjectStatus.PENDING)
    technology: Optional[str] = Field(None, description="Detected technology (java, python, etc)")
    created_at: datetime = Field(default_factory=datetime.now)
    knowledge_bases: List[str] = Field(default_factory=list, description="List of KB folder IDs")

class KnowledgeBase(BaseModel):
    id: str = Field(..., description="Unique KB ID")
    project_id: str = Field(..., description="Parent project ID")
    name: str = Field(..., description="KB folder name")
    description: Optional[str] = Field(None)
    file_count: int = Field(default=0)
    indexed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.now)

class TicketInfo(BaseModel):
    ticket_id: str
    title: str
    description: str
    priority: str
    labels: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)

class WorkflowEvent(BaseModel):
    type: str  # agent_start, agent_complete, rag_query, llm_call, state_update, error
    timestamp: datetime = Field(default_factory=datetime.now)
    agent: Optional[str] = None
    message: str
    data: Optional[Dict[str, Any]] = None

# ============================================================================
# PERSISTENT STORAGE
# ============================================================================

_PROJECTS_FILE = Path(__file__).parent / "projects.json"


def _load_projects() -> Dict[str, "Project"]:
    """Load projects from disk (survives restarts)."""
    if _PROJECTS_FILE.exists():
        try:
            raw = json.loads(_PROJECTS_FILE.read_text())
            return {pid: Project(**p) for pid, p in raw.items()}
        except Exception as exc:
            logger.warning(f"Could not load projects.json: {exc}")
    return {}


def _save_projects(proj_dict: Dict[str, "Project"]):
    """Persist projects to disk."""
    try:
        _PROJECTS_FILE.write_text(
            json.dumps({pid: json.loads(p.model_dump_json()) for pid, p in proj_dict.items()}, indent=2)
        )
    except Exception as exc:
        logger.warning(f"Could not save projects.json: {exc}")


projects: Dict[str, Project] = _load_projects()
knowledge_bases: Dict[str, KnowledgeBase] = {}
active_workflows: Dict[str, List[WorkflowEvent]] = {}

# WebSocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to WebSocket: {e}")

manager = ConnectionManager()

# ============================================================================
# API ENDPOINTS - PROJECTS
# ============================================================================

class CreateProjectRequest(BaseModel):
    name: str
    type: ProjectType
    path: str
    technology: Optional[str] = None

@app.post("/api/projects", response_model=Project)
async def create_project(request: CreateProjectRequest):
    """Create a new project (local or git)"""
    import uuid
    
    name = request.name
    type = request.type
    path = request.path
    technology = request.technology
    
    project_id = str(uuid.uuid4())
    
    project = Project(
        id=project_id,
        name=name,
        type=type,
        path=path,
        technology=technology,
        status=ProjectStatus.PENDING
    )
    
    # If git project, clone it
    if type == ProjectType.GIT:
        try:
            project.status = ProjectStatus.CLONING
            projects[project_id] = project
            
            # Broadcast cloning started
            await manager.broadcast({
                "type": "project_status",
                "project_id": project_id,
                "status": "cloning",
                "message": f"Cloning repository: {path}"
            })
            
            # Clone to workspace
            clone_dir = Path("workspace") / project_id
            clone_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Cloning {path} to {clone_dir}")
            Repo.clone_from(path, clone_dir)
            
            project.local_path = str(clone_dir)
            project.status = ProjectStatus.READY
            
            await manager.broadcast({
                "type": "project_status",
                "project_id": project_id,
                "status": "ready",
                "message": f"Repository cloned successfully"
            })
            
        except Exception as e:
            project.status = ProjectStatus.ERROR
            logger.error(f"Error cloning repository: {e}")
            await manager.broadcast({
                "type": "project_status",
                "project_id": project_id,
                "status": "error",
                "message": f"Clone failed: {str(e)}"
            })
            raise HTTPException(status_code=500, detail=f"Clone failed: {str(e)}")
    else:
        # Local project - verify path exists and is a directory
        path_obj = Path(path)
        if not path_obj.exists():
            raise HTTPException(status_code=400, detail=f"Path not found: {path}. Please check if the directory exists.")
        if not path_obj.is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {path}")
        project.local_path = str(path_obj.absolute())
        project.status = ProjectStatus.READY
    
    projects[project_id] = project
    _save_projects(projects)
    return project

@app.get("/api/projects", response_model=List[Project])
async def list_projects():
    """Get all projects"""
    return list(projects.values())

@app.get("/api/projects/{project_id}", response_model=Project)
async def get_project(project_id: str):
    """Get project by ID"""
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    return projects[project_id]

@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project"""
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Delete associated knowledge bases
    kb_ids_to_delete = [kb_id for kb_id, kb in knowledge_bases.items() if kb.project_id == project_id]
    for kb_id in kb_ids_to_delete:
        del knowledge_bases[kb_id]
    
    del projects[project_id]
    _save_projects(projects)
    return {"message": "Project deleted successfully"}

# ============================================================================
# API ENDPOINTS - CHAT & TASK HISTORY (durable, survives restarts)
# ============================================================================

class SaveChatRequest(BaseModel):
    chat_id: Optional[str] = None
    project_id: Optional[str] = None
    title: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    workflow_id: Optional[str] = None


@app.get("/api/history/chats")
async def api_list_chats(project_id: Optional[str] = None):
    """List previous conversations (newest first), optionally by project."""
    return history_store.list_chats(project_id)


@app.get("/api/history/chats/{chat_id}")
async def api_get_chat(chat_id: str):
    """Load a full conversation with all messages."""
    chat = history_store.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@app.post("/api/history/chats")
async def api_save_chat(request: SaveChatRequest):
    """Create or update a conversation (auto-save from the UI)."""
    record = history_store.upsert_chat(
        chat_id=request.chat_id,
        project_id=request.project_id,
        messages=request.messages,
        title=request.title,
        workflow_id=request.workflow_id,
    )
    return {"id": record["id"], "title": record["title"], "updated_at": record["updated_at"]}


@app.delete("/api/history/chats/{chat_id}")
async def api_delete_chat(chat_id: str):
    if not history_store.delete_chat(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"message": "Chat deleted"}


@app.get("/api/history/tasks")
async def api_list_tasks(project_id: Optional[str] = None):
    """List completed tasks/runs (newest first), optionally by project."""
    return history_store.list_tasks(project_id)


@app.delete("/api/history/tasks/{workflow_id}")
async def api_delete_task(workflow_id: str):
    if not history_store.delete_task(workflow_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Task deleted"}

@app.post("/api/projects/{project_id}/index")
async def index_project(project_id: str):
    """
    Index a project - parse AST, extract symbols, build graph database.
    This should be called after adding a project and before entering tickets.
    """
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project = projects[project_id]
    repo_path = Path(project.local_path or project.path)
    
    if not repo_path.exists():
        raise HTTPException(status_code=400, detail=f"Project path not found: {repo_path}")
    
    try:
        # Update project status
        project.status = ProjectStatus.INDEXING
        
        logger.info(f"🔍 Starting indexing for project {project.name} at {repo_path}")
        
        # Broadcast indexing started
        await manager.broadcast({
            "type": "project_status",
            "project_id": project_id,
            "status": "indexing",
            "message": "Starting repository indexing..."
        })
        
        # Import indexer components
        import sys
        aviator_platform_path = Path(__file__).parent.parent.parent.parent / "aviator-platform"
        if str(aviator_platform_path) not in sys.path:
            sys.path.insert(0, str(aviator_platform_path))
            
        aviator_adt_path = Path(__file__).parent.parent.parent.parent / "aviator_adt" / "src"
        if str(aviator_adt_path) not in sys.path:
            sys.path.insert(0, str(aviator_adt_path))
        
        from aviator_core.indexer import index_repository
        from aviator_core.storage.sqlite_store import SqliteStore
        
        # Prepare index database path
        index_path = repo_path / ".aviator" / "index.db"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Get the event loop for thread-safe broadcasts
        loop = asyncio.get_event_loop()
        
        # Progress callback for UI updates (matches indexer.py signature: current_file, done, total)
        def progress_callback(current_file: Path, done: int, total: int):
            # Use run_coroutine_threadsafe for thread-safe async calls
            progress_percent = int((done / total) * 100) if total > 0 else 0
            logger.info(f"Indexing progress: {done}/{total} ({progress_percent}%) - {current_file.name}")
            
            coro = manager.broadcast({
                "type": "indexing_progress",
                "project_id": project_id,
                "files_scanned": done,
                "total_files": total,
                "progress_percent": progress_percent,
                "current_file": str(current_file.name),
                "message": f"📝 Indexing: {done}/{total} files ({progress_percent}%) - {current_file.name}"
            })
            asyncio.run_coroutine_threadsafe(coro, loop)
        
        # Wrapper function that creates store in the background thread (SQLite thread safety)
        def run_indexing():
            # Create SqliteStore in this thread
            store = SqliteStore(str(index_path))
            try:
                # Run indexing
                return index_repository(
                    Path(repo_path),
                    store,
                    progress=progress_callback
                )
            finally:
                # Close the store connection
                store.close()
        
        # Run indexing in background thread
        stats = await asyncio.to_thread(run_indexing)
        
        # Log the actual results (PROOF it really worked!)
        logger.info(f"✅ Indexing complete for {project.name}:")
        logger.info(f"   📁 Files scanned: {stats.files_scanned}")
        logger.info(f"   ✔️  Files parsed: {stats.files_parsed}")
        logger.info(f"   ❌ Files failed: {stats.files_failed}")
        logger.info(f"   🔤 Symbols extracted: {stats.symbols}")
        logger.info(f"   🔗 Edges (relationships): {stats.edges}")
        logger.info(f"   ⏱️  Time taken: {stats.duration_seconds:.2f}s")
        
        # ── Phase 2: Vector embedding indexing (for RAG) ────────────────────
        logger.info("🧠 Starting vector embedding indexing (RAG)...")
        
        await manager.broadcast({
            "type": "indexing_progress",
            "project_id": project_id,
            "files_scanned": stats.files_scanned,
            "total_files": stats.files_scanned,
            "progress_percent": 100,
            "current_file": "Generating embeddings for RAG...",
            "message": "🧠 Generating vector embeddings for RAG retrieval..."
        })

        def run_vector_indexing():
            """Build smart class-level documents from SQLite index and embed them.

            Instead of raw 1500-char file chunks this produces one structured
            document per class that includes: package, stereotype, REST endpoints,
            @Autowired dependencies, method signatures, and a source code excerpt.
            This makes RAG dramatically more accurate for questions like
            "which endpoint creates an area?" or "what does AreaService depend on?".
            """
            import json as _json
            from langchain_core.documents import Document as LCDocument
            from aviator.vector_store import VectorStoreManager
            from aviator_core.storage import SqliteStore
            from pathlib import Path as _Path

            db_path = _Path(repo_path) / ".aviator" / "index.db"
            store_sq = SqliteStore(db_path)
            conn = store_sq._conn
            conn.row_factory = __import__("sqlite3").Row

            manager_vs = VectorStoreManager()
            code_store = manager_vs.get(schema_name="codebase_knowledge")

            # Delete old vectors for this project
            try:
                code_store.delete(filter={"source_project": str(repo_path)})
            except Exception:
                pass

            docs: list[LCDocument] = []

            # ── 1. Class-level structured documents ─────────────────────────
            classes = conn.execute(
                "SELECT * FROM symbols WHERE kind IN ('class','interface','enum') "
                "ORDER BY path, start_line"
            ).fetchall()

            for cls in classes:
                cls_id = cls["id"]
                cls_name = cls["qualified_name"] or cls["name"]
                stereotype = cls["spring_stereotype"] or ""
                package = cls["package"] or ""
                file_path = cls["path"]
                is_feign = bool(cls["is_feign_client"])
                feign_svc = cls["feign_service_name"] or ""

                # Collect methods for this class
                methods = conn.execute(
                    "SELECT name, signature, return_type, spring_endpoints, annotations "
                    "FROM symbols WHERE parent_id = ? AND kind = 'method' "
                    "ORDER BY start_line",
                    (cls_id,),
                ).fetchall()

                # Collect @Autowired fields
                fields = conn.execute(
                    "SELECT name, return_type, spring_dependencies "
                    "FROM symbols WHERE parent_id = ? AND kind = 'field' "
                    "AND spring_dependencies != '[]' ORDER BY start_line",
                    (cls_id,),
                ).fetchall()

                # Build document text
                lines = []
                header = f"# {cls_name}"
                if stereotype:
                    header += f" (@{stereotype})"
                if is_feign:
                    header += f" [FeignClient -> {feign_svc}]"
                lines.append(header)
                lines.append(f"File: {file_path}")
                if package:
                    lines.append(f"Package: {package}")

                # REST endpoints
                all_endpoints = []
                for m in methods:
                    try:
                        eps = _json.loads(m["spring_endpoints"] or "[]")
                        all_endpoints.extend(eps)
                    except Exception:
                        pass
                if all_endpoints:
                    lines.append("\nREST Endpoints:")
                    for ep in all_endpoints:
                        lines.append(f"  - {ep}")

                # Dependencies
                if fields:
                    lines.append("\nDependencies (@Autowired):")
                    for f in fields:
                        type_name = f["return_type"] or f["name"]
                        lines.append(f"  - {type_name}")

                # Methods
                if methods:
                    lines.append("\nMethods:")
                    for m in methods:
                        sig = m["signature"] or m["name"]
                        ret = m["return_type"] or ""
                        try:
                            anns = _json.loads(m["annotations"] or "[]")
                            ann_str = " ".join(f"@{a}" for a in anns if a not in (
                                "Override", "SuppressWarnings", "Deprecated"
                            ))
                        except Exception:
                            ann_str = ""
                        method_line = f"  - {sig}"
                        if ret:
                            method_line += f" -> {ret}"
                        if ann_str:
                            method_line += f"  [{ann_str}]"
                        lines.append(method_line)

                # Optionally include a short raw source excerpt
                try:
                    abs_path = _Path(repo_path) / file_path
                    source_lines = abs_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    start = max(0, cls["start_line"] - 1)
                    end = min(len(source_lines), cls["end_line"])
                    excerpt_lines = source_lines[start:end]
                    # Limit to 40 lines so document stays focused
                    if len(excerpt_lines) > 40:
                        excerpt_lines = excerpt_lines[:40] + ["    // ... (truncated)"]
                    if excerpt_lines:
                        lines.append(f"\n```java\n" + "\n".join(excerpt_lines) + "\n```")
                except Exception:
                    pass

                content = "\n".join(lines)
                if len(content.strip()) < 30:
                    continue

                docs.append(LCDocument(
                    page_content=content,
                    metadata={
                        "file_path": file_path,
                        "source": file_path,
                        "source_project": str(repo_path),
                        "type": "class_summary",
                        "language": "java",
                        "class_name": cls["name"],
                        "spring_stereotype": stereotype,
                    }
                ))

            # ── 2. Project architecture document (pom.xml / app config) ─────
            config = store_sq.get_project_config()
            if config:
                arch_lines = ["# Project Architecture & Configuration\n"]
                for k, v in sorted(config.items()):
                    arch_lines.append(f"- **{k}**: {v}")
                arch_doc = LCDocument(
                    page_content="\n".join(arch_lines),
                    metadata={
                        "source": "project_config",
                        "source_project": str(repo_path),
                        "type": "architecture",
                        "language": "config",
                    }
                )
                # Embed in architectural_guidelines schema too
                try:
                    arch_store = manager_vs.get(schema_name="architectural_guidelines")
                    arch_store.delete(filter={"source_project": str(repo_path)})
                    arch_store.add_documents([arch_doc])
                except Exception as e:
                    logger.warning(f"arch store embed failed: {e}")
                docs.append(arch_doc)

            # ── 3. Embed all documents ───────────────────────────────────────
            BATCH = 50
            total_embedded = 0
            for i in range(0, len(docs), BATCH):
                batch = docs[i: i + BATCH]
                code_store.add_documents(batch)
                total_embedded += len(batch)
                logger.info(f"  Embedded {total_embedded}/{len(docs)} docs")

            return total_embedded, len(docs)

        try:
            embedded, total_chunks = await asyncio.to_thread(run_vector_indexing)
            logger.info(f"✅ Vector indexing complete: {embedded}/{total_chunks} chunks embedded")
            stats_extra = {"vector_chunks": embedded}
        except Exception as e:
            logger.error(f"Vector indexing failed (non-fatal): {e}", exc_info=True)
            stats_extra = {"vector_chunks": 0, "vector_error": str(e)}

        # ── Phase 3: Neo4j graph indexing ─────────────────────────────────────
        logger.info("🌐 Starting Neo4j graph indexing...")
        await manager.broadcast({
            "type": "indexing_progress",
            "project_id": project_id,
            "files_scanned": stats.files_scanned,
            "total_files": stats.files_scanned,
            "progress_percent": 100,
            "current_file": "Building code relationship graph in Neo4j...",
            "message": "🌐 Populating Neo4j code graph (relationships, call chains)..."
        })

        def run_neo4j_indexing():
            """Read symbols/edges from SQLite and push to Neo4j."""
            import sqlite3 as _sqlite3
            import json as _json
            from aviator_core.models import (
                FileRecord, Symbol, SymbolKind, Edge, EdgeKind, SourceLocation, IndexStats
            )
            from aviator_core.storage.neo4j_store import Neo4jStore

            neo4j = Neo4jStore(workspace_path=str(repo_path))  # reads NEO4J_PASSWORD from env

            try:
                conn = _sqlite3.connect(str(index_path))
                conn.row_factory = _sqlite3.Row

                # --- Files ---
                file_rows = conn.execute("SELECT * FROM files").fetchall()
                for r in file_rows:
                    neo4j.upsert_file(FileRecord(
                        path=r["path"], language=r["language"],
                        package=r["package"] or "", sha256=r["sha256"],
                        size_bytes=r["size_bytes"],
                        parse_ok=bool(r["parse_ok"]),
                        parse_error=r["parse_error"],
                    ))

                # --- Symbols (in batches) ---
                sym_rows = conn.execute("SELECT * FROM symbols").fetchall()
                symbols: list[Symbol] = []
                for r in sym_rows:
                    try:
                        symbols.append(Symbol(
                            id=r["id"],
                            kind=SymbolKind(r["kind"]),
                            name=r["name"],
                            qualified_name=r["qualified_name"],
                            package=r["package"],
                            parent_id=r["parent_id"],
                            location=SourceLocation(
                                path=r["path"],
                                start_line=r["start_line"] or 1,
                                end_line=r["end_line"] or 1,
                                start_col=r["start_col"] or 1,
                                end_col=r["end_col"] or 1,
                            ),
                            signature=r["signature"],
                            return_type=r["return_type"],
                            modifiers=_json.loads(r["modifiers"] or "[]"),
                            annotations=_json.loads(r["annotations"] or "[]"),
                            parameter_types=_json.loads(r["parameter_types"] or "[]"),
                            spring_stereotype=r["spring_stereotype"],
                            spring_endpoints=_json.loads(r["spring_endpoints"] or "[]"),
                            spring_dependencies=_json.loads(r["spring_dependencies"] or "[]"),
                            is_feign_client=bool(r["is_feign_client"]),
                            feign_service_name=r["feign_service_name"],
                        ))
                    except Exception as exc:
                        logger.warning(f"Skip symbol {r['id']}: {exc}")
                        continue

                BATCH = 500
                for i in range(0, len(symbols), BATCH):
                    neo4j.insert_symbols(symbols[i:i + BATCH])
                logger.info(f"  Neo4j: {len(symbols)} symbols pushed")

                # --- Edges ---
                edge_rows = conn.execute("SELECT * FROM edges").fetchall()
                edges: list[Edge] = []
                for r in edge_rows:
                    try:
                        edges.append(Edge(
                            kind=EdgeKind(r["kind"]),
                            src_id=r["src_id"],
                            dst_id=r["dst_id"],
                            dst_name=r["dst_name"],
                            location=SourceLocation(
                                path=r["path"] or "",
                                start_line=r["start_line"] or 1,
                            ) if r["path"] else None,
                        ))
                    except Exception as exc:
                        logger.warning(f"Skip edge: {exc}")
                        continue

                for i in range(0, len(edges), BATCH):
                    neo4j.insert_edges(edges[i:i + BATCH])
                logger.info(f"  Neo4j: {len(edges)} edges pushed")

                conn.close()
                return len(symbols), len(edges)

            finally:
                neo4j.close()

        try:
            neo4j_symbols, neo4j_edges = await asyncio.to_thread(run_neo4j_indexing)
            logger.info(f"✅ Neo4j indexing complete: {neo4j_symbols} nodes, {neo4j_edges} edges")
            stats_extra["neo4j_nodes"] = neo4j_symbols
            stats_extra["neo4j_edges"] = neo4j_edges
        except Exception as e:
            logger.error(f"Neo4j indexing failed (non-fatal): {e}", exc_info=True)
            stats_extra["neo4j_error"] = str(e)

        # ── Phase 4: Repository Brain generation ──────────────────────────────
        # Create the directory brain now (at index time) so Ticket-to-Code CREATE
        # localization has project knowledge from the very first ticket. Non-fatal.
        logger.info("🧠 Starting Repository Brain generation...")
        await manager.broadcast({
            "type": "indexing_progress",
            "project_id": project_id,
            "files_scanned": stats.files_scanned,
            "total_files": stats.files_scanned,
            "progress_percent": 100,
            "current_file": "Generating repository brain (directory intelligence)...",
            "message": "🧠 Generating Repository Brain (directory map + LLM 'why')..."
        })

        def run_brain_generation():
            import sys as _sys
            src_path = Path(__file__).parent.parent.parent / "src"
            if str(src_path) not in _sys.path:
                _sys.path.insert(0, str(src_path))
            from ticket_to_code.brain.generate_repository_brain import ensure_repository_brain
            try:
                from aviator.services.llm import LLMRegistry
                brain_llm = LLMRegistry.get_llm()
            except Exception:
                brain_llm = None
            return ensure_repository_brain(str(repo_path), llm=brain_llm)

        try:
            brain_summary = await asyncio.to_thread(run_brain_generation)
            logger.info(f"✅ Repository Brain: {brain_summary}")
            stats_extra["brain_status"] = brain_summary.get("bootstrap", brain_summary.get("status"))
            stats_extra["brain_directories"] = brain_summary.get("directories", 0)
        except Exception as e:
            logger.error(f"Repository Brain generation failed (non-fatal): {e}", exc_info=True)
            stats_extra["brain_error"] = str(e)

        # ── Update status & broadcast completion ─────────────────────────────
        project.status = ProjectStatus.INDEXED
        _save_projects(projects)
        
        # Broadcast completion (include stats so frontend can display them)
        vector_chunks = stats_extra.get("vector_chunks", 0)
        neo4j_nodes  = stats_extra.get("neo4j_nodes",   0)
        neo4j_edges_n = stats_extra.get("neo4j_edges",  0)
        brain_status = stats_extra.get("brain_status", "unknown")
        brain_dirs   = stats_extra.get("brain_directories", 0)
        await manager.broadcast({
            "type": "project_status",
            "project_id": project_id,
            "status": "indexed",
            "message": (
                f"✅ Indexed {stats.files_scanned} files | "
                f"{stats.symbols} symbols | {stats.edges} edges | "
                f"{stats.resolved_calls} resolved calls | "
                f"{vector_chunks} RAG chunks | "
                f"Neo4j: {neo4j_nodes} nodes, {neo4j_edges_n} rels | "
                f"Brain: {brain_status} ({brain_dirs} dirs)"
            ),
            "stats": {
                "files_scanned":  stats.files_scanned,
                "files_parsed":   stats.files_parsed,
                "files_failed":   stats.files_failed,
                "symbols":        stats.symbols,
                "edges":          stats.edges,
                "resolved_calls": stats.resolved_calls,
                "vector_chunks":  vector_chunks,
                "neo4j_nodes":    neo4j_nodes,
                "neo4j_edges":    neo4j_edges_n,
                "brain_status":   brain_status,
                "brain_directories": brain_dirs,
            }
        })

        return {
            "message": "Indexing complete",
            "stats": {
                "files_scanned":  stats.files_scanned,
                "files_parsed":   stats.files_parsed,
                "files_failed":   stats.files_failed,
                "symbols":        stats.symbols,
                "edges":          stats.edges,
                "resolved_calls": stats.resolved_calls,
                "vector_chunks":  vector_chunks,
                "neo4j_nodes":    neo4j_nodes,
                "neo4j_edges":    neo4j_edges_n,
                "brain_status":   brain_status,
                "brain_directories": brain_dirs,
            }
        }
        
    except Exception as e:
        project.status = ProjectStatus.ERROR
        logger.error(f"Indexing error: {e}", exc_info=True)
        
        await manager.broadcast({
            "type": "project_status",
            "project_id": project_id,
            "status": "error",
            "message": f"Indexing failed: {str(e)}"
        })
        
        raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)}")

# ============================================================================
# API ENDPOINTS - KNOWLEDGE BASES
# ============================================================================

@app.post("/api/projects/{project_id}/knowledge-bases", response_model=KnowledgeBase)
async def create_knowledge_base(
    project_id: str,
    name: str,
    description: Optional[str] = None
):
    """Create a knowledge base folder for a project"""
    import uuid
    
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    kb_id = str(uuid.uuid4())
    kb = KnowledgeBase(
        id=kb_id,
        project_id=project_id,
        name=name,
        description=description
    )
    
    knowledge_bases[kb_id] = kb
    projects[project_id].knowledge_bases.append(kb_id)
    
    return kb

@app.get("/api/projects/{project_id}/knowledge-bases", response_model=List[KnowledgeBase])
async def list_knowledge_bases(project_id: str):
    """Get all knowledge bases for a project"""
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return [kb for kb in knowledge_bases.values() if kb.project_id == project_id]

@app.post("/api/knowledge-bases/{kb_id}/upload")
async def upload_files(kb_id: str, files: List[UploadFile] = File(...)):
    """Upload files to knowledge base"""
    if kb_id not in knowledge_bases:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    
    kb = knowledge_bases[kb_id]
    kb_dir = Path("workspace") / "kb" / kb_id
    kb_dir.mkdir(parents=True, exist_ok=True)
    
    uploaded_files = []
    for file in files:
        file_path = kb_dir / file.filename
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        uploaded_files.append(file.filename)
    
    kb.file_count += len(uploaded_files)
    
    return {
        "kb_id": kb_id,
        "uploaded": uploaded_files,
        "total_files": kb.file_count
    }

@app.post("/api/knowledge-bases/{kb_id}/index")
async def index_knowledge_base(kb_id: str):
    """Index knowledge base files into RAG"""
    if kb_id not in knowledge_bases:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    
    kb = knowledge_bases[kb_id]
    
    # TODO: Integrate with existing RAG indexing
    # This will use aviator_adt vector store
    
    await manager.broadcast({
        "type": "kb_status",
        "kb_id": kb_id,
        "status": "indexing",
        "message": f"Indexing {kb.file_count} files..."
    })
    
    # Simulate indexing (replace with real implementation)
    await asyncio.sleep(2)
    
    kb.indexed = True
    
    await manager.broadcast({
        "type": "kb_status",
        "kb_id": kb_id,
        "status": "indexed",
        "message": f"Knowledge base indexed successfully"
    })
    
    return {"message": "Knowledge base indexed", "kb_id": kb_id}

# ============================================================================
# API ENDPOINTS - WORKFLOW
# ============================================================================
# ============================================================================

@app.post("/api/workflow/start")
async def start_workflow(
    project_id: str,
    kb_id: str,
    ticket_info: TicketInfo
):
    """Start autonomous workflow for a ticket"""
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if kb_id not in knowledge_bases:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    
    project = projects[project_id]
    kb = knowledge_bases[kb_id]
    
    workflow_id = f"{project_id}_{ticket_info.ticket_id}"
    active_workflows[workflow_id] = []
    
    # Send initial event
    event = WorkflowEvent(
        type="workflow_started",
        message=f"Starting workflow for ticket: {ticket_info.ticket_id}",
        data={
            "project": project.name,
            "kb": kb.name,
            "ticket": ticket_info.dict()
        }
    )
    active_workflows[workflow_id].append(event)
    
    await manager.broadcast({
        "type": "workflow_event",
        "workflow_id": workflow_id,
        "event": event.dict()
    })
    
    # TODO: Integrate with existing ticket_to_code workflow
    # This will call run_autonomous_workflow() with real-time updates
    
    return {
        "workflow_id": workflow_id,
        "status": "started",
        "ticket": ticket_info.dict()
    }

@app.get("/api/workflow/{workflow_id}/events", response_model=List[WorkflowEvent])
async def get_workflow_events(workflow_id: str):
    """Get all events for a workflow"""
    if workflow_id not in active_workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    return active_workflows[workflow_id]

# ============================================================================
# WEBSOCKET ENDPOINT
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates"""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and receive messages
            data = await websocket.receive_text()
            logger.info(f"Received from client: {data}")
            
            # Echo back for testing
            await websocket.send_json({
                "type": "ping",
                "message": "Connection active"
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# ============================================================================
# WORKFLOW ENDPOINTS - Transparent, streaming workflow execution
# ============================================================================

import queue
import threading

# Per-workflow state and WebSocket connections
workflow_status: Dict[str, Any] = {}
workflow_connections: Dict[str, List[WebSocket]] = {}


def _extract_workflow_outputs(final_state: Dict[str, Any]) -> Dict[str, Any]:
    """Extract authoritative workflow outputs used for explanation assembly."""
    contracts = final_state.get("internal_node_contracts") or []
    planner_decisions = final_state.get("planner_decisions") or {}
    grounded = final_state.get("grounded_implementation_decision")
    generated_code = final_state.get("generated_code") or []

    owner_files: List[str] = []
    if grounded is not None:
        file_resolutions = getattr(grounded, "file_resolutions", None)
        if file_resolutions is None and isinstance(grounded, dict):
            file_resolutions = grounded.get("file_resolutions", [])
        for item in file_resolutions or []:
            if isinstance(item, dict):
                strategy = str(item.get("strategy", ""))
                file_path = str(item.get("file_path", ""))
            else:
                strategy = str(getattr(item, "strategy", ""))
                file_path = str(getattr(item, "file_path", ""))
            if strategy.endswith("DIRECT_OWNER") and file_path:
                owner_files.append(file_path)

    generated_files = []
    for g in generated_code:
        if isinstance(g, dict):
            fp = g.get("file_path")
        else:
            fp = getattr(g, "file_path", None)
        if fp:
            generated_files.append(str(fp))

    build_attribution = final_state.get("build_attribution")
    if isinstance(build_attribution, dict):
        build_reasoning = str(build_attribution.get("reasoning") or "")
    else:
        build_reasoning = str(getattr(build_attribution, "reasoning", "") or "")

    learning_update = final_state.get("learning_update") or {}
    decision_ledger = final_state.get("decision_ledger") or []
    confidence_history = final_state.get("confidence_history") or []
    assumptions_active = final_state.get("assumptions_active") or []
    assumptions_invalidated = final_state.get("assumptions_invalidated") or []
    outcome_verification = final_state.get("outcome_verification") or {}

    return {
        "contracts": contracts[-14:],
        "decision_ledger": decision_ledger[-20:],
        "decision_counter": int(final_state.get("decision_counter") or len(decision_ledger)),
        "overall_confidence": float(final_state.get("overall_confidence") or 0.70),
        "confidence_history": confidence_history[-30:],
        "confidence_drop_streak": int(final_state.get("confidence_drop_streak") or 0),
        "assumptions_active": assumptions_active[-20:],
        "assumptions_invalidated": assumptions_invalidated[-30:],
        "outcome_verification": outcome_verification if isinstance(outcome_verification, dict) else {},
        "planner_decisions_count": len(planner_decisions),
        "owner_files": owner_files[:10],
        "generated_files": generated_files[:12],
        "validation_failure_reason": str(final_state.get("validation_failure_reason") or ""),
        "build_attribution_reasoning": build_reasoning[:320],
        "learning_update": learning_update if isinstance(learning_update, dict) else {},
        "convergence_stagnation_count": int(final_state.get("convergence_stagnation_count") or 0),
    }


def _build_workflow_explanation(state: Dict[str, Any], workflow_output: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build user-facing explanation from per-node contracts + authoritative outputs."""
    status = state.get("status", "running")
    phase = state.get("current_phase", "processing")
    steps = state.get("steps", []) or []
    workflow_output = workflow_output or {}

    contracts = workflow_output.get("contracts") or []
    decision_ledger = workflow_output.get("decision_ledger") or []
    overall_confidence = float(workflow_output.get("overall_confidence") or 0.70)
    confidence_history = workflow_output.get("confidence_history") or []
    assumptions_active = workflow_output.get("assumptions_active") or []
    assumptions_invalidated = workflow_output.get("assumptions_invalidated") or []
    contract_decisions = []
    contract_reasons = []
    contract_evidence = []
    for c in contracts:
        if not isinstance(c, dict):
            continue
        decision = str(c.get("decision") or "").strip()
        reason = str(c.get("reason") or "").strip()
        node = str(c.get("node") or "").strip()
        if decision:
            contract_decisions.append(f"{node}: {decision}" if node else decision)
        if reason:
            contract_reasons.append(reason)
        for e in (c.get("evidence") or []):
            txt = str(e).strip()
            if txt:
                contract_evidence.append(txt)

    decisions: List[str] = []
    blockers: List[str] = []
    for step in steps[-20:]:
        if not isinstance(step, dict):
            continue
        msg = str(step.get("message") or "").strip()
        output = step.get("data", {}).get("agent_output") if isinstance(step.get("data"), dict) else None
        if output:
            first_line = str(output).splitlines()[0].strip()
            if first_line:
                decisions.append(first_line)
        elif msg:
            decisions.append(msg)

        if step.get("status") == "error" and msg:
            blockers.append(msg)

    ledger_decisions = []
    for item in decision_ledger[-6:]:
        if not isinstance(item, dict):
            continue
        node = str(item.get("node") or "")
        dec = str(item.get("decision") or "")
        if dec:
            ledger_decisions.append(f"{node}: {dec}" if node else dec)

    decisions = ledger_decisions + contract_decisions + decisions

    validation_reason = str(state.get("validation_failure_reason") or "").strip()
    if validation_reason:
        blockers.append(validation_reason)
    authoritative_validation = str(workflow_output.get("validation_failure_reason") or "").strip()
    if authoritative_validation and authoritative_validation not in blockers:
        blockers.append(authoritative_validation)

    if contract_reasons:
        blockers.extend(contract_reasons[:2] if status == "failed" else [])

    if state.get("error"):
        blockers.append(str(state.get("error")))

    if assumptions_invalidated:
        latest_invalid = assumptions_invalidated[-1]
        if isinstance(latest_invalid, dict):
            reason = str(latest_invalid.get("invalidated_reason") or "").strip()
            if reason:
                blockers.append(f"Assumption invalidated: {reason}")

    # Deduplicate while preserving order.
    def _dedupe(items: List[str], limit: int) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in items:
            key = item.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= limit:
                break
        return out

    debug_decisions = _dedupe(decisions, 40)
    decisions = debug_decisions[:5]
    blockers = _dedupe(blockers, 5)
    evidence = _dedupe(contract_evidence, 5)

    planner_decisions_count = int(workflow_output.get("planner_decisions_count") or 0)
    owner_files = workflow_output.get("owner_files") or []
    generated_files = workflow_output.get("generated_files") or []
    build_reasoning = str(workflow_output.get("build_attribution_reasoning") or "").strip()
    outcome_verification = workflow_output.get("outcome_verification") or {}
    # Pull outcome_check_result from state (post-build semantic validation)
    outcome_check = state.get("outcome_check_result") or {}
    stagnation = int(workflow_output.get("convergence_stagnation_count") or 0)
    confidence_drop_streak = int(workflow_output.get("confidence_drop_streak") or 0)

    if status == "completed":
        check_verdict = outcome_check.get("status", "")
        if check_verdict == "CORRECT":
            summary = "The workflow completed successfully and the generated code correctly implements all ticket requirements."
            why = "Build passed and semantic validation confirmed all functional requirements are satisfied."
        elif check_verdict == "PARTIAL":
            summary = "The workflow completed (build passed) but some ticket requirements may be partially implemented."
            why = outcome_check.get("details", "")[:200] or "Semantic check found partial implementation."
        elif check_verdict == "INCOMPLETE":
            summary = "The workflow completed (build passed) but the generated code is missing key requirements."
            why = outcome_check.get("details", "")[:200] or "Semantic check found incomplete implementation."
        else:
            summary = "The workflow converged to a valid implementation and completed generation plus validation."
            why = "Node contracts and validation outputs indicate sufficient evidence, grounded owner targeting, and no terminal blockers."
        next_action = "Review generated files and merge after human verification."
    elif status == "failed":
        summary = "The workflow stopped because it could not converge to a stable solution path."
        why = blockers[0] if blockers else "A convergence, quality, or runtime budget guard was triggered."
        next_action = "Refine ticket scope or provide stronger owner-file hints, then rerun."
    else:
        summary = "The workflow is actively converging on the solution by iterating discovery, planning, and validation."
        why = f"Current phase: {phase}. The system is still collecting or validating evidence."
        next_action = "Wait for completion or inspect current agent decisions below."

    details = []
    details.append(f"Overall confidence: {overall_confidence:.2f}")
    if planner_decisions_count:
        details.append(f"Planner evaluated {planner_decisions_count} candidate decision(s).")
    if owner_files:
        details.append(f"Grounded owners: {', '.join(owner_files[:4])}")
    if generated_files:
        details.append(f"Generated files: {', '.join(generated_files[:4])}")
    if outcome_check:
        verdict = outcome_check.get("status", "")
        if verdict in ("CORRECT", "PARTIAL", "INCOMPLETE"):
            details.append(f"Semantic validation: {verdict} — {outcome_check.get('details', '')[:120]}")
    if outcome_verification:
        verified = bool(outcome_verification.get("verified"))
        details.append(f"Outcome verification: {'passed' if verified else 'failed'}")
    if build_reasoning:
        details.append(f"Build attribution: {build_reasoning}")
    if stagnation > 0:
        details.append(f"Convergence stagnation counter: {stagnation}")
    if confidence_drop_streak > 0:
        details.append(f"Low-confidence streak: {confidence_drop_streak}")

    confidence_trend = []
    for c in confidence_history[-6:]:
        if not isinstance(c, dict):
            continue
        n = str(c.get("node") or "unknown")
        oc = c.get("overall_confidence")
        if isinstance(oc, (int, float)):
            confidence_trend.append(f"{n}: {float(oc):.2f}")

    active_assumptions = []
    for a in assumptions_active[-5:]:
        if not isinstance(a, dict):
            continue
        txt = str(a.get("text") or "").strip()
        if txt:
            active_assumptions.append(txt)

    invalidated_assumptions = []
    for a in assumptions_invalidated[-5:]:
        if not isinstance(a, dict):
            continue
        txt = str(a.get("text") or "").strip()
        if txt:
            invalidated_assumptions.append(txt)

    return {
        "summary": summary,
        "why": why,
        "decisions": decisions,
        "decision_debug": debug_decisions,
        "decision_summary": {
            "total": int(workflow_output.get("decision_counter") or len(decision_ledger)),
            "top_decisions": decisions,
        },
        "evidence": evidence,
        "details": details[:5],
        "confidence_trend": confidence_trend,
        "active_assumptions": active_assumptions,
        "invalidated_assumptions": invalidated_assumptions,
        "decision_count": int(workflow_output.get("decision_counter") or len(decision_ledger)),
        "blockers": blockers,
        "next_action": next_action,
        "phase": phase,
        "status": status,
    }


async def broadcast_to_workflow(workflow_id: str, message: dict):
    """Broadcast a step update to all WebSocket clients watching this workflow."""
    # Persist step for late-joining clients
    if workflow_id in workflow_status:
        workflow_status[workflow_id].setdefault("steps", []).append(message)
        # Update current phase
        if "phase" in message:
            workflow_status[workflow_id]["current_phase"] = message["phase"]

    for ws in list(workflow_connections.get(workflow_id, [])):
        try:
            await ws.send_json(message)
        except Exception:
            workflow_connections.get(workflow_id, []).remove(ws)


def _map_node_to_phase(node_name: str) -> str:
    """Map LangGraph node name to UI phase label."""
    mapping = {
        "investigate": "classification",
        "investigation": "classification",
        "classify": "classification",
        "localize": "localization",
        "localization": "localization",
        "rag": "context_loading",
        "rag_context": "context_loading",
        "test_rag": "context_loading",
        "code_rag": "context_loading",
        "generate_tests": "patch_generation",
        "generate_code": "patch_generation",
        "build": "validation",
        "test": "validation",
        "validate": "validation",
        "commit": "committing",
    }
    for key, phase in mapping.items():
        if key in node_name.lower():
            return phase
    return "classification"


def _extract_rag_files(state: dict) -> List[dict]:
    """Extract file list from RAG context in workflow state."""
    files = []
    for ctx_key in ("code_rag_context", "test_rag_context"):
        ctx = state.get(ctx_key)
        if not ctx:
            continue
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except Exception:
                continue
        if isinstance(ctx, list):
            for item in ctx:
                path = None
                score = 0.0
                if isinstance(item, dict):
                    path = item.get("file_path") or item.get("path") or item.get("source")
                    score = item.get("similarity_score") or item.get("score") or 0.0
                elif isinstance(item, str):
                    path = item
                elif hasattr(item, "file_path"):
                    path = item.file_path
                    score = getattr(item, "similarity_score", 0.0) or 0.0
                if path:
                    files.append({"path": path, "selected": True, "confidence": float(score)})
        elif isinstance(ctx, dict):
            for path in ctx.get("files", []):
                files.append({"path": path, "selected": True, "confidence": 0.9})
    return files


def _extract_rag_chunks_detailed(state_delta: dict) -> List[dict]:
    """Extract RAG chunks with full detail (score, content preview, metadata) for UI display.

    Returns chunks sorted by similarity_score descending so highest-relevance chunks
    appear first.  The UI uses this to show the user exactly which code was fed to
    the LLM and let them exclude low-quality chunks.
    """
    chunks: list[dict] = []
    for ctx_key in ("test_rag_context", "code_rag_context"):
        raw = state_delta.get(ctx_key) or []
        rag_label = "Test Behavior" if ctx_key == "test_rag_context" else "Architecture"
        for c in raw:
            try:
                if isinstance(c, dict):
                    score   = float(c.get("similarity_score") or c.get("score") or 0)
                    fpath   = c.get("file_path") or c.get("path") or "unknown"
                    content = c.get("content", "")
                    name    = c.get("name", "")
                    ctype   = str(c.get("chunk_type", ""))
                elif hasattr(c, "file_path"):
                    score   = float(getattr(c, "similarity_score", 0) or 0)
                    fpath   = c.file_path
                    content = getattr(c, "content", "")
                    name    = getattr(c, "name", "")
                    ctype   = str(getattr(c, "chunk_type", ""))
                else:
                    continue
                chunks.append({
                    "path":       fpath,
                    "score":      round(score, 3),
                    "name":       name,
                    "chunk_type": ctype,
                    "preview":    content[:300].strip() if content else "",
                    "rag_label":  rag_label,
                    "included":   True,
                })
            except Exception:
                continue
    chunks.sort(key=lambda x: x["score"], reverse=True)
    return chunks


def _serialize_agent_output(node_name: str, state_delta: dict) -> Optional[str]:
    """Produce a human-readable summary of what each LangGraph node produced.

    Returns a multi-line string shown in the per-step agent-output panel in the UI,
    or None when there is nothing interesting to show.
    """
    try:
        if node_name == "investigate":
            inv = state_delta.get("investigation_result")
            if not inv:
                return None
            ticket_type  = str(getattr(inv, "ticket_type", getattr(inv, "get", lambda k, d=None: d)("ticket_type", "?")))
            confidence   = getattr(inv, "confidence",   0.0)
            affected     = getattr(inv, "affected_systems", []) or []
            explanation  = getattr(inv, "explanation", "") or ""
            action       = getattr(inv, "recommended_action", "") or ""
            lines = [
                f"🏷️  Type: {ticket_type}",
                f"📊 Confidence: {float(confidence):.0%}",
            ]
            if affected:
                lines.append(f"🎯 Affected: {', '.join(str(a) for a in affected[:5])}")
            if explanation:
                lines.append(f"\n📝 {explanation[:500]}")
            if action:
                lines.append(f"\n✅ Next action: {action[:200]}")
            return "\n".join(lines)

        elif node_name == "unified_analysis":
            req = state_delta.get("requirements")
            if not req:
                return None
            func_reqs    = getattr(req, "functional_requirements", []) or []
            affected_comp = getattr(req, "affected_components", []) or []
            edge_cases   = getattr(req, "edge_cases", []) or []
            lines = ["📋 Functional Requirements:"]
            for r in func_reqs[:6]:
                lines.append(f"  • {str(r)}")
            if affected_comp:
                lines.append(f"\n🔧 Affected Components: {', '.join(str(c) for c in affected_comp[:8])}")
            if edge_cases:
                lines.append(f"\n⚠️  Edge Cases identified: {len(edge_cases)}")
                for ec in edge_cases[:3]:
                    lines.append(f"  • {str(ec)}")
            return "\n".join(lines)

        elif node_name == "plan":
            plan = state_delta.get("architectural_plan")
            if not plan:
                return None
            pattern  = str(getattr(plan, "pattern", "?"))
            tasks    = getattr(plan, "tasks", []) or []
            modules  = getattr(plan, "affected_modules", []) or []
            lines    = [f"🏗️  Architectural Pattern: {pattern}"]
            if modules:
                lines.append(f"📦 Modules: {', '.join(str(m) for m in modules[:6])}")
            if tasks:
                lines.append(f"\n📝 Tasks ({len(tasks)}):") 
                for t in tasks[:6]:
                    title    = getattr(t, "title", str(t))
                    ttype    = str(getattr(t, "task_type", ""))
                    complexity = getattr(t, "complexity", "")
                    row = f"  [{ttype}] {title}"
                    if complexity:
                        row += f"  (complexity {complexity})"
                    lines.append(row)
            return "\n".join(lines)

        elif node_name in ("rag_tests", "rag_code"):
            chunks = _extract_rag_chunks_detailed(state_delta)
            if not chunks:
                return "⚠️  No relevant context retrieved from RAG"
            icon  = "🧪" if node_name == "rag_tests" else "🏗️"
            label = "Test Behavior" if node_name == "rag_tests" else "Architecture"
            lines = [f"{icon} {label} RAG — {len(chunks)} chunks retrieved (sorted by relevance):"]
            for ch in chunks[:8]:
                bar   = "█" * int(ch["score"] * 10) + "░" * (10 - int(ch["score"] * 10))
                fname = ch["path"].split("/")[-1]
                name  = f" ({ch['name']})" if ch["name"] else ""
                lines.append(f"  {bar} {ch['score']:.0%}  {fname}{name}")
            return "\n".join(lines)

        elif node_name in ("generate_tests", "generate_code"):
            key = "generated_tests" if node_name == "generate_tests" else "generated_code"
            gen = state_delta.get(key) or []
            if not gen:
                return None
            icon  = "🧪" if node_name == "generate_tests" else "💻"
            lines = [f"{icon} Generated {len(gen)} file(s):"]
            for g in gen:
                if isinstance(g, dict):
                    fpath   = g.get("file_path", "?")
                    change  = g.get("change_type", "")
                elif hasattr(g, "file_path"):
                    fpath   = g.file_path
                    change  = str(getattr(g, "change_type", ""))
                else:
                    continue
                basename = fpath.split("/")[-1] if "/" in fpath else fpath.split("\\")[-1] if "\\" in fpath else fpath
                action_icon = "✨" if "create" in str(change).lower() else "✏️"
                lines.append(f"  {action_icon} {basename}")
            lines.append("\n→ View full changes in the Changes tab")
            return "\n".join(lines)

        elif node_name == "build":
            res = state_delta.get("build_result")
            if not res:
                return None
            status = str(getattr(res, "status", "?"))
            errors = getattr(res, "errors", []) or []
            if errors:
                import re as _re
                # Group errors by file for readability
                error_groups = {}
                for e in errors:
                    e_str = str(e)
                    # Extract filename from error like "src/app/file.ts(12,3): error TS..."
                    file_match = _re.match(r'(?:.*?[\\/])?([^\\/(]+\.\w+)', e_str)
                    fname = file_match.group(1) if file_match else "other"
                    error_groups.setdefault(fname, []).append(e_str)
                lines = [f"🔨 Build: {status} — {len(errors)} error(s) in {len(error_groups)} file(s):"]
                for fname, errs in sorted(error_groups.items()):
                    lines.append(f"  📄 {fname}: {len(errs)} error(s)")
                    for err in errs[:3]:  # show up to 3 per file
                        # Extract just the error code + message
                        err_msg = err
                        ts_match = _re.search(r'error (TS\d+:.*)', err)
                        if ts_match:
                            err_msg = ts_match.group(1)[:120]
                        else:
                            err_msg = err[-120:]
                        lines.append(f"    ❌ {err_msg}")
                    if len(errs) > 3:
                        lines.append(f"    ... and {len(errs) - 3} more")
                return "\n".join(lines)
            return f"🔨 Build: {status} ✅"

        elif node_name == "test":
            res = state_delta.get("test_result")
            if not res:
                return None
            status  = str(getattr(res, "status", "?"))
            passed  = getattr(res, "passed_tests", 0) or 0
            failed  = getattr(res, "failed_tests", 0) or 0
            return f"🧪 Tests: {status}  ✅ {passed} passed  ❌ {failed} failed"

    except Exception as exc:
        logger.debug(f"_serialize_agent_output({node_name}) error: {exc}")
    return None


# REMOVED workflow_manager import - use direct calls to run_autonomous_workflow()
# from workflow_manager import workflow_manager
# from workflow_models import WorkflowPhase

UPLOAD_DIR = Path("C:/aviator_traces/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload screenshot, log file, or document attachment for workflow analysis."""
    try:
        unique_name = f"{uuid.uuid4().hex[:8]}_{file.filename}"
        dest_path = UPLOAD_DIR / unique_name
        contents = await file.read()
        with open(dest_path, "wb") as f:
            f.write(contents)
        logger.info(f"📁 Uploaded attachment saved to: {dest_path} ({len(contents)} bytes)")
        return {
            "filename": file.filename,
            "filepath": str(dest_path),
            "size": len(contents),
            "content_type": file.content_type,
        }
    except Exception as e:
        logger.error(f"Failed to upload file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class StartTransparentWorkflowRequest(BaseModel):
    project_id: str
    ticket_id: str
    ticket_description: str
    repo_path: str
    execution_mode: ExecutionMode = ExecutionMode.PIPELINE
    attachments: List[str] = Field(default_factory=list)


class StartReasoningAgentRequest(BaseModel):
    ticket_id: str
    ticket_title: Optional[str] = None
    ticket_description: str
    repo_path: str
    technology: Optional[str] = None
    attachments: List[str] = Field(default_factory=list)


@app.post("/api/agent/cc4e/solve")
async def solve_with_cc4e_reasoner(request: StartReasoningAgentRequest):
    """Deprecated standalone endpoint. Use transparent workflow with execution_mode."""
    raise HTTPException(
        status_code=410,
        detail=(
            "Deprecated endpoint. Use /api/workflow/transparent/start with "
            "execution_mode='reasoning' for experimental evaluation, or "
            "execution_mode='pipeline' for production runs."
        ),
    )


@app.get("/api/agent/cc4e/brain")
async def get_cc4e_brain_summary(repo_path: str):
    """Return a summary of what the CC4E Brain has learned so far."""
    if os.getenv("AVIATOR_ENABLE_EXPERIMENTAL_REASONING", "0") != "1":
        raise HTTPException(
            status_code=403,
            detail=(
                "CC4E Brain endpoint is disabled in production mode. "
                "Set AVIATOR_ENABLE_EXPERIMENTAL_REASONING=1 for evaluation-only access."
            ),
        )
    try:
        from ticket_to_code.reasoning import CC4EBrain
        brain = CC4EBrain(repo_path)
        return brain.summary()
    except Exception as e:
        logger.error(f"CC4E brain summary failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/workflow/transparent/start")
async def start_transparent_workflow(request: StartTransparentWorkflowRequest):
    """
    Start autonomous workflow using existing ticket_to_code system.
    """
    try:
        if (
            request.execution_mode == ExecutionMode.REASONING
            and os.getenv("AVIATOR_ENABLE_EXPERIMENTAL_REASONING", "0") != "1"
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "execution_mode='reasoning' is experimental and disabled by default. "
                    "Set AVIATOR_ENABLE_EXPERIMENTAL_REASONING=1 for evaluation-only runs."
                ),
            )

        workflow_id = str(uuid.uuid4())
        active_workflows[workflow_id] = []
        
        # Log the full incoming ticket payload for reproducibility and debugging
        try:
            log_dir = Path(request.repo_path) / ".aviator" / "tickets"
            log_dir.mkdir(parents=True, exist_ok=True)
            ticket_log_path = log_dir / f"{workflow_id}.json"
            if hasattr(request, "model_dump_json"):
                payload_str = request.model_dump_json(indent=2)
            elif hasattr(request, "dict"):
                payload_str = json.dumps(request.dict(), indent=2, default=str)
            else:
                payload_str = json.dumps(getattr(request, "__dict__", {}), indent=2, default=str)
            with open(ticket_log_path, "w", encoding="utf-8") as f:
                f.write(payload_str)
            logger.info(f"Saved full incoming ticket payload to {ticket_log_path}")
        except Exception as e:
            logger.error(f"Failed to save incoming ticket payload: {e}")
            
        # Build a ticket from the user's description (no external service needed)
        auto_id = f"TASK-{workflow_id[:8].upper()}"
        ticket = ValueEdgeTicket(
            ticket_id=auto_id,
            title=request.ticket_description[:100],
            description=request.ticket_description,
            priority=TicketPriority.MEDIUM,
            labels=["feature"],
            attachments=request.attachments or []
        )
        
        # Store initial status
        workflow_status[workflow_id] = {
            "status": "running",
            "ticket_id": auto_id,
            "project_id": request.project_id,
            "repo_path": request.repo_path,
            "execution_mode": request.execution_mode.value,
            "workflow_explanation": None,
            "workflow_output": {},
            "started_at": datetime.now().isoformat()
        }
        
        # Run the EXISTING autonomous workflow in background
        asyncio.create_task(
            run_workflow_async(
                workflow_id,
                ticket,
                request.repo_path,
                request.execution_mode.value,
            )
        )
        
        return {
            "workflow_id": workflow_id,
            "status": "started",
            "execution_mode": request.execution_mode.value,
            "message": "Autonomous workflow started..."
        }
    
    except Exception as e:
        logger.error(f"Failed to start workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

async def run_workflow_async(
    workflow_id: str,
    ticket: "ValueEdgeTicket",
    workspace_path: str,
    execution_mode: str = "pipeline",
):
    """
    Run the LangGraph workflow in a background thread.
    Per-node updates are broadcast via asyncio.run_coroutine_threadsafe so the
    UI gets live progress without waiting for the full workflow to complete.
    """
    loop = asyncio.get_event_loop()

    def put(step: dict):
        """Thread-safe broadcast helper — schedules coroutine on the event loop."""
        asyncio.run_coroutine_threadsafe(broadcast_to_workflow(workflow_id, step), loop)

    done_event = threading.Event()
    result_holder: dict = {}

    # --- Node name → UI phase mapping ---
    # Keys MUST match the names passed to workflow.add_node(...) in workflow.py
    PHASE_MAP = {
        # Actual node names registered in create_ticket_to_code_graph()
        "investigate":       ("classification",   "🔍 Investigating & classifying ticket"),
        "runtime_diagnosis": ("classification",   "🩺 Enhancement 1: Analyzing runtime logs"),
        "unified_analysis":  ("requirements",     "📋 Analyzing requirements"),
        "plan":              ("planning",         "🗺️ Planning implementation"),
        "rag_tests":         ("context_loading",  "📂 Loading test-behavior context (RAG)"),
        "rag_code":          ("context_loading",  "📂 Loading architecture context (RAG)"),
        "generate_tests":    ("patch_generation", "✍️ Generating test cases (TDD)"),
        "generate_code":     ("patch_generation", "✍️ Generating implementation code"),
        "edit_loop":         ("patch_generation", "✏️ Adaptive edit loop (file-by-file)"),
        "patch_gate":        ("validation",       "🛡️ Patch safety check"),
        "angular_module_registration": ("patch_generation", "📦 Angular module registration"),
        "build":             ("validation",       "🔨 Building project"),
        "outcome_verification": ("validation",    "🎯 Verifying requested outcome"),
        "outcome_check":     ("validation",       "🎯 Outcome verification"),
        "test":              ("validation",       "✅ Running tests"),
        "fix_build":         ("patch_generation", "🔧 Fixing build errors (w/ Enh 4+12)"),
        "fix_test":          ("patch_generation", "🔧 Fixing test failures"),
    }

    def thread_run():
        try:
            if execution_mode == ExecutionMode.REASONING.value:
                from ticket_to_code.workflow import run_autonomous_workflow

                final_state = run_autonomous_workflow(
                    ticket=ticket,
                    workspace_path=workspace_path,
                    technology=None,
                    max_fix_attempts=3,
                    execution_mode="reasoning",
                )
                result_holder["state"] = final_state
                return

            # NOTE: We previously mocked aviator.vector_store here because langchain-postgres 
            # was missing in some environments, which caused RAG to silently return 0 results.
            # Do NOT restore the mock; instead, ensure langchain-postgres is installed.

            from ticket_to_code.workflow import create_ticket_to_code_graph, _set_transient
            from langgraph.checkpoint.memory import MemorySaver

            put({"phase": "classification", "status": "in_progress",
                 "message": "🔍 Agent 1: Investigating and classifying ticket…",
                 "data": None, "timestamp": datetime.now().isoformat()})

            wf_graph = create_ticket_to_code_graph(workspace_path)
            memory  = MemorySaver()
            app     = wf_graph.compile(checkpointer=memory)

            # Register UI callback in transient registry (NOT in state — functions crash msgpack)
            _set_transient(ticket.ticket_id, "_ui_callback", put)

            initial_state = {
                "ticket": ticket, "workspace_path": workspace_path,
                "max_retry_attempts": 3, "investigation_result": None,
                "requirements": None, "architectural_plan": None,
                "solution_guidance": None, "test_rag_context": None,
                "code_rag_context": None, "test_status": None,
                "code_status": None, "generated_tests": None,
                "generated_code": None, "build_result": None,
                "test_result": None, "retry_attempt": 0,
                "convergence_fingerprint_history": [],
                "convergence_stagnation_count": 0,
                "convergence_last_progress_score": 0.0,
                "decision_ledger": [],
                "decision_counter": 0,
                "overall_confidence": 0.70,
                "confidence_history": [],
                "confidence_drop_streak": 0,
                "confidence_evidence_score": 0.0,
                "assumptions_active": [],
                "assumptions_invalidated": [],
                "assumption_sequence": 0,
                "assumption_dependency_index": {},
                "invalidated_assumption_ids": [],
                "impacted_nodes": [],
                "outcome_verification": None,
                "runtime_diagnosis": None,
                "last_error_type": None, "status": "initialized",
                "errors": [], "start_time": datetime.now(), "end_time": None,
            }
            config = {"configurable": {"thread_id": ticket.ticket_id}}

            aggregate_state: Dict[str, Any] = {}
            for node_update in app.stream(initial_state, config):
                for node_name, state_delta in node_update.items():
                    if not isinstance(state_delta, dict):
                        continue
                    aggregate_state.update(state_delta)
                    phase, label = PHASE_MAP.get(node_name, ("processing", f"⚙️ {node_name}"))

                    # Update current_phase in shared state so polling reflects it
                    workflow_status[workflow_id]["current_phase"] = phase

                    # Build enriched step message
                    inv         = state_delta.get("investigation_result")
                    rfiles      = _extract_rag_files(state_delta)
                    rag_chunks  = _extract_rag_chunks_detailed(state_delta)
                    agent_out   = _serialize_agent_output(node_name, state_delta)
                    gen         = state_delta.get("generated_code") or state_delta.get("generated_tests")

                    if inv and node_name == "investigate":
                        inv_type = getattr(inv, "ticket_type", str(inv))
                        conf     = getattr(inv, "confidence", "?")
                        msg = f"🔍 Classified as: {inv_type}  (confidence {conf})"
                    elif rag_chunks:
                        top = rag_chunks[0]
                        msg = (f"📂 RAG: {len(rag_chunks)} chunks "
                               f"(top: {top['path'].split('/')[-1]} {top['score']:.0%})")
                        # Also merge file-level candidates
                        existing = workflow_status[workflow_id].get("candidate_files", [])
                        seen     = {f["path"] for f in existing}
                        workflow_status[workflow_id]["candidate_files"] = existing + [
                            {"path": ch["path"], "selected": True, "confidence": ch["score"]}
                            for ch in rag_chunks if ch["path"] not in seen
                        ]
                    elif rfiles:
                        names = ", ".join(f["path"].split("/")[-1] for f in rfiles[:4])
                        msg   = f"📂 RAG: {len(rfiles)} file(s) — {names}"
                        existing = workflow_status[workflow_id].get("candidate_files", [])
                        seen     = {f["path"] for f in existing}
                        workflow_status[workflow_id]["candidate_files"] = existing + [
                            rf for rf in rfiles if rf["path"] not in seen
                        ]
                    elif gen and isinstance(gen, list):
                        names = [g.get("file_path", str(g)) if isinstance(g, dict) else str(g) for g in gen[:3]]
                        msg   = f"✍️ Generated {len(gen)} file(s): " + ", ".join(names)
                        workflow_status[workflow_id]["generated_files"] = [
                            g.get("file_path", str(g)) if isinstance(g, dict) else str(g)
                            for g in gen
                        ]
                    else:
                        msg = agent_out.split("\n")[0] if agent_out else label

                    put({
                        "phase": phase, "status": "in_progress", "message": msg,
                        "data": {
                            "node":         node_name,
                            "rag_files":    rfiles or None,
                            "rag_chunks":   rag_chunks or None,
                            "investigation": str(inv)[:400] if inv else None,
                            "agent_output": agent_out,
                        },
                        "timestamp": datetime.now().isoformat(),
                    })

            snapshot = app.get_state(config)
            final_state = snapshot.values if hasattr(snapshot, "values") else aggregate_state
            if not isinstance(final_state, dict):
                final_state = aggregate_state

            result_holder["state"] = final_state

        except Exception as exc:
            result_holder["error"] = str(exc)
            logger.error(f"Workflow thread error: {exc}", exc_info=True)
            put({
                "phase": "failed",
                "status": "error",
                "message": f"❌ Workflow crashed: {str(exc)}",
                "data": None,
                "timestamp": datetime.now().isoformat()
            })
        finally:
            done_event.set()

    t = threading.Thread(target=thread_run, daemon=True)
    t.start()

    if execution_mode == ExecutionMode.REASONING.value:
        await broadcast_to_workflow(workflow_id, {
            "phase": "processing",
            "status": "in_progress",
            "message": "🧪 Running experimental reasoning mode",
            "data": {"execution_mode": execution_mode},
            "timestamp": datetime.now().isoformat(),
        })

    # Yield control to event loop while waiting for the thread
    while not done_event.wait(timeout=0):
        await asyncio.sleep(0.5)

    if "error" in result_holder:
        err = result_holder["error"]
        workflow_status[workflow_id].update({"status": "failed", "current_phase": "failed", "error": err})
        workflow_status[workflow_id]["workflow_explanation"] = _build_workflow_explanation(
            workflow_status[workflow_id],
            workflow_status[workflow_id].get("workflow_output", {}),
        )
        await broadcast_to_workflow(workflow_id, {
            "phase": "failed", "status": "error",
            "message": f"❌ Workflow failed: {err}",
            "data": {"workflow_explanation": workflow_status[workflow_id].get("workflow_explanation")},
            "timestamp": datetime.now().isoformat(),
        })
        return

    # --- Workflow completed ---
    final = result_holder.get("state") or {}
    if final and len(final) == 1 and isinstance(list(final.values())[0], dict):
        final = list(final.values())[0]

    workflow_output = _extract_workflow_outputs(final if isinstance(final, dict) else {})
    workflow_status[workflow_id]["workflow_output"] = workflow_output
    if workflow_output.get("validation_failure_reason"):
        workflow_status[workflow_id]["validation_failure_reason"] = workflow_output.get("validation_failure_reason")

    gen_code  = final.get("generated_code")  or []
    gen_tests = final.get("generated_tests") or []
    all_gen = [g.get("file_path", str(g)) if isinstance(g, dict) else str(g) for g in gen_code + gen_tests]
    if all_gen:
        workflow_status[workflow_id]["generated_files"] = all_gen

    rag_files = workflow_status[workflow_id].get("candidate_files", []) or _extract_rag_files(final)
    if rag_files:
        workflow_status[workflow_id]["candidate_files"] = rag_files

    final_status = str(final.get("status", "")).strip().lower() if isinstance(final, dict) else ""
    validation_failure_reason = workflow_output.get("validation_failure_reason")
    build_result = final.get("build_result") if isinstance(final, dict) else None
    build_err = None
    build_failed = False
    if build_result:
        if hasattr(build_result, "status"):
            status_val = build_result.status.value if hasattr(build_result.status, "value") else build_result.status
            if status_val == "failure":
                build_failed = True
        elif isinstance(build_result, dict) and build_result.get("status") == "failure":
            build_failed = True

        if hasattr(build_result, "errors") and build_result.errors:
            build_err = "Build failed after max retries:\n" + "\n".join(build_result.errors)
        elif isinstance(build_result, dict) and build_result.get("errors"):
            build_err = "Build failed after max retries:\n" + "\n".join(build_result.get("errors"))

    terminal_failed = final_status in ["failed", "build_failed", "test_failed"] or bool(validation_failure_reason) or build_failed

    workflow_status[workflow_id].update({
        "status": "failed" if terminal_failed else "completed",
        "current_phase": "failed" if terminal_failed else "completed",
        "execution_mode": execution_mode,
        "completed_at": datetime.now().isoformat(),
        "error": (final.get("error") if isinstance(final, dict) else None) or validation_failure_reason or build_err,
    })
    workflow_status[workflow_id]["workflow_explanation"] = _build_workflow_explanation(
        workflow_status[workflow_id],
        workflow_output,
    )

    # Update ticket file in .aviator/tickets with final workflow results
    try:
        ticket_log_path = Path(workspace_path) / ".aviator" / "tickets" / f"{workflow_id}.json"
        ticket_data = {}
        if ticket_log_path.exists() and ticket_log_path.stat().st_size > 0:
            with open(ticket_log_path, "r", encoding="utf-8") as f:
                ticket_data = json.load(f)
        ticket_data.update({
            "ticket_id": ticket.ticket_id,
            "status": "failed" if terminal_failed else "completed",
            "completed_at": datetime.now().isoformat(),
            "generated_files": all_gen,
            "candidate_files": rag_files,
            "error": workflow_status[workflow_id].get("error"),
        })
        with open(ticket_log_path, "w", encoding="utf-8") as f:
            json.dump(ticket_data, f, indent=2)
        logger.info(f"Saved completed ticket summary to {ticket_log_path}")
    except Exception as e:
        logger.warning(f"Failed to update ticket record in .aviator/tickets: {e}")

    # Persist a durable task-history record so it appears in the UI history panel.
    try:
        history_store.record_task(
            workflow_id=workflow_id,
            project_id=workflow_status[workflow_id].get("project_id"),
            ticket_id=ticket.ticket_id,
            title=ticket.title,
            description=ticket.description,
            status="failed" if terminal_failed else "completed",
            changed_files=all_gen,
            execution_mode=execution_mode,
            error=workflow_status[workflow_id].get("error"),
        )
    except Exception as e:
        logger.warning(f"Failed to record task history: {e}")

    if terminal_failed:
        await broadcast_to_workflow(workflow_id, {
            "phase": "failed", "status": "error",
            "message": (
                f"❌ Workflow ended with terminal failure"
                f" ({validation_failure_reason or final_status or 'unknown reason'})"
            ),
            "data": {
                "generated_code": all_gen,
                "candidate_files": rag_files,
                "workflow_explanation": workflow_status[workflow_id].get("workflow_explanation"),
                "validation_failure_reason": validation_failure_reason,
            },
            "timestamp": datetime.now().isoformat(),
        })
    else:
        await broadcast_to_workflow(workflow_id, {
            "phase": "completed", "status": "completed",
            "message": f"✅ Workflow complete — {len(gen_code)} code file(s), {len(gen_tests)} test file(s) generated",
            "data": {
                "generated_code": all_gen,
                "candidate_files": rag_files,
                "workflow_explanation": workflow_status[workflow_id].get("workflow_explanation"),
            },
            "timestamp": datetime.now().isoformat(),
        })



@app.get("/api/workflow/transparent/{workflow_id}")
async def get_workflow_status(workflow_id: str):
    """
    Return the current workflow state in the shape the frontend TransparentWorkflow
    component expects: current_phase, candidate_files, steps, operation_approved, etc.
    """
    if workflow_id not in workflow_status:
        raise HTTPException(status_code=404, detail="Workflow not found")

    state = workflow_status[workflow_id]
    state["workflow_explanation"] = _build_workflow_explanation(
        state,
        state.get("workflow_output", {}),
    )

    return {
        "workflow_id": workflow_id,
        "ticket_id": state.get("ticket_id", ""),
        "status": state.get("status", "running"),
        "execution_mode": state.get("execution_mode", ExecutionMode.PIPELINE.value),
        "current_phase": state.get("current_phase", "classification"),
        "candidate_files": state.get("candidate_files", []),
        "generated_files": state.get("generated_files", []),
        "operation_approved": state.get("operation_approved", False),
        "steps": state.get("steps", []),
        "error": state.get("error"),
        "workflow_explanation": state.get("workflow_explanation"),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
    }


@app.get("/api/workflow/transparent/{workflow_id}/steps")
async def get_workflow_steps(workflow_id: str):
    """Get all workflow steps."""
    if workflow_id not in workflow_status:
        raise HTTPException(status_code=404, detail="Workflow not found")
    state = workflow_status[workflow_id]
    return {
        "workflow_id": workflow_id,
        "current_phase": state.get("current_phase", "classification"),
        "steps": state.get("steps", []),
    }


class ApproveFilesRequest(BaseModel):
    workflow_id: str
    selected_files: List[str]


class ContinueToGenerationRequest(BaseModel):
    workflow_id: str
    repo_path: str


class ApplyPatchRequest(BaseModel):
    workflow_id: str
    repo_path: str


class RunTestsRequest(BaseModel):
    workflow_id: str
    repo_path: str
    test_pattern: str = "**/*Test*.java"


class ApproveOperationRequest(BaseModel):
    workflow_id: str


@app.post("/api/workflow/transparent/approve-operation")
async def approve_operation(request: ApproveOperationRequest):
    """Record that user approved the classified operation type."""
    wid = request.workflow_id
    if wid in workflow_status:
        workflow_status[wid]["operation_approved"] = True
    return {"status": "approved"}


@app.post("/api/workflow/transparent/approve-files")
async def approve_files(request: ApproveFilesRequest):
    """Record which files the user approved for the workflow."""
    wid = request.workflow_id
    if wid not in workflow_status:
        raise HTTPException(status_code=404, detail="Workflow not found")
    workflow_status[wid]["selected_files"] = request.selected_files
    return {
        "status": "approved",
        "message": f"Recorded {len(request.selected_files)} approved file(s).",
    }


@app.post("/api/workflow/transparent/continue-to-generation")
async def continue_to_generation(request: ContinueToGenerationRequest):
    """Informational — workflow runs autonomously."""
    return {"status": "running", "message": "Workflow is running autonomously."}


@app.post("/api/workflow/transparent/apply-patch")
async def apply_patch(request: ApplyPatchRequest):
    """Informational — patches are applied as part of the workflow."""
    return {"status": "running", "message": "Patches are applied by the autonomous workflow."}


@app.post("/api/workflow/transparent/run-tests")
async def run_tests(request: RunTestsRequest):
    """Informational — tests are run as part of the workflow."""
    return {"status": "running", "message": "Tests are executed by the autonomous workflow."}


@app.websocket("/ws/workflow/{workflow_id}")
async def workflow_websocket(websocket: WebSocket, workflow_id: str):
    """
    Per-workflow WebSocket.  The TransparentWorkflow component connects here to
    receive live step updates as the LangGraph workflow executes.
    """
    await websocket.accept()
    workflow_connections.setdefault(workflow_id, []).append(websocket)

    # Send any steps that were emitted before this client connected
    existing_steps = workflow_status.get(workflow_id, {}).get("steps", [])
    for step in existing_steps:
        try:
            await websocket.send_json(step)
        except Exception:
            break

    try:
        while True:
            await websocket.receive_text()   # keep alive; ignore client messages
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Workflow WebSocket error for {workflow_id}: {e}")
    finally:
        conns = workflow_connections.get(workflow_id, [])
        if websocket in conns:
            conns.remove(websocket)


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "projects": len(projects),
        "knowledge_bases": len(knowledge_bases),
        "active_workflows": len(workflow_status),
        "websocket_connections": len(manager.active_connections),
    }


@app.get("/api/file-tree")
async def get_file_tree(path: str):
    """Return a simple file-tree for the given directory path."""
    root = Path(path)
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    IGNORE = {".git", ".aviator", "target", "build", "node_modules", "__pycache__", ".idea"}
    MAX_DEPTH = 4

    def build_tree(p: Path, depth: int) -> dict:
        if depth > MAX_DEPTH:
            return None
        if p.is_file():
            return {"name": p.name, "type": "file", "path": str(p.relative_to(root)).replace("\\", "/")}
        children = []
        try:
            for child in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name)):
                if child.name in IGNORE:
                    continue
                node = build_tree(child, depth + 1)
                if node:
                    children.append(node)
        except PermissionError:
            pass
        return {"name": p.name, "type": "folder", "children": children}

    return build_tree(root, 0)


# ============================================================================
# STARTUP
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    logger.info("🚀 Aviator Chatbot API starting...")
    Path("workspace").mkdir(exist_ok=True)
    Path("workspace/kb").mkdir(exist_ok=True)
    logger.info("✅ Aviator Chatbot API ready!")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
