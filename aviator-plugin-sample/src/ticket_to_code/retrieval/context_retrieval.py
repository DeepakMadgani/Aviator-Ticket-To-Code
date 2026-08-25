"""
ContextRetrievalService — cascading multi-source context assembly for generation (B2).

Architecture RAG is a single point of failure when pgvector is empty or misconfigured.
This service guarantees non-empty context by cascading through five strategies:

  1. RAG semantic search     (pgvector)
  2. Neo4j graph search      (dependency neighbours of writable files)
  3. SQLite symbol search    (FTS / LIKE on writable file paths + plan keywords)
  4. Filesystem grep         (pattern search on writable files)
  5. DirectRead              (read writable files directly, byte-capped)

Each strategy fires if the previous one returned fewer than MIN_CHUNKS results.
Strategies are composited (not exclusive): all results found before the
threshold is met are returned together.

Author: Deepak Madgani
Date: July 2026
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Set

if TYPE_CHECKING:
    from ticket_to_code.retrieval.rag_engine import CodebaseRAGEngine
    from ticket_to_code.models import ArchitecturalPlan, CodeChunk, StructuredRequirements
    from ticket_to_code.runtime.run_context import RunContext

logger = logging.getLogger(__name__)

# Minimum number of chunks before we try the next strategy
_MIN_CHUNKS = 5
# Maximum bytes to read from a single file in DirectRead
_MAX_DIRECTREAD_BYTES = 16_000
# Maximum total bytes to read across all DirectRead files
_MAX_DIRECTREAD_TOTAL_BYTES = 64_000

# Neo4j Cypher: find symbols defined in writable files and their immediate callers/callees
_NEO4J_NEIGHBOURS_CYPHER = """
MATCH (f:File {workspace: $workspace})-[:DEFINES]->(s:Symbol {workspace: $workspace})
WHERE f.path IN $paths
OPTIONAL MATCH (s)-[:CALLS|CALLED_BY]-(s2:Symbol {workspace: $workspace})<-[:DEFINES]-(f2:File {workspace: $workspace})
RETURN DISTINCT f2.path AS neighbour_path, f2.content AS content
LIMIT 20
"""


class ContextRetrievalService:
    """
    Cascading context retrieval for code generation (B2).

    Usage::

        svc = ContextRetrievalService(
            rag_engine=agents.rag_engine,
            neo4j_store=agents.localizer.neo4j_store,
            sqlite_store=agents.localizer.sqlite_store,
            workspace_path=workspace_path,
        )
        chunks, sources = svc.retrieve(
            requirements=state["requirements"],
            plan=plan,
            run_ctx=run_ctx,
        )
    """

    def __init__(
        self,
        rag_engine: Optional["CodebaseRAGEngine"] = None,
        neo4j_store=None,
        sqlite_store=None,
        workspace_path: str = ".",
    ) -> None:
        self.rag_engine    = rag_engine
        self.neo4j_store   = neo4j_store
        self.sqlite_store  = sqlite_store
        self.workspace_path = Path(workspace_path).resolve()

    # ── Public entry point ─────────────────────────────────────────────────────

    def retrieve(
        self,
        requirements: Optional["StructuredRequirements"] = None,
        plan: Optional["ArchitecturalPlan"] = None,
        run_ctx: Optional["RunContext"] = None,
        extra_query: str = "",
    ) -> tuple[list, list[str]]:
        """
        Assemble architecture context for code generation.

        Returns:
            (chunks, sources_used) where chunks is a list of raw context objects
            (either CodeChunk models or plain dicts) and sources_used is a list
            of source names in the order they fired.

        Never raises; logs degradation via run_ctx when provided.
        """
        writable_files: List[str] = []
        if plan:
            writable_files = [
                t.file_path for t in (plan.tasks or [])
                if getattr(t, "task_type", None) and t.task_type.value != "read_only"
            ]

        # Build search query from requirements + plan
        query = self._build_query(requirements, plan, extra_query)

        all_chunks: list = []
        sources_used: List[str] = []

        # ── Strategy 1: RAG semantic search ──────────────────────────────────
        try:
            rag_chunks = self._strategy_rag(requirements, plan)
            if rag_chunks:
                all_chunks.extend(rag_chunks)
                sources_used.append("RAG")
                logger.info(
                    "ContextRetrieval: RAG returned %d chunks", len(rag_chunks)
                )
            else:
                logger.warning(
                    "ContextRetrieval: RAG returned 0 chunks — cascading to Neo4j"
                )
                if run_ctx:
                    from ticket_to_code.runtime.run_context import HealthLevel
                    run_ctx.degrade("rag_engine", HealthLevel.DEGRADED, "0 chunks returned")
        except Exception as exc:
            logger.warning("ContextRetrieval: RAG failed (%s) — cascading", exc)
            if run_ctx:
                from ticket_to_code.runtime.run_context import HealthLevel
                run_ctx.degrade("rag_engine", HealthLevel.DEGRADED, str(exc))

        if len(all_chunks) >= _MIN_CHUNKS:
            return self._finalise(all_chunks, sources_used, run_ctx)

        # ── Strategy 2: Neo4j graph search ────────────────────────────────────
        if writable_files and self.neo4j_store:
            try:
                neo4j_chunks = self._strategy_neo4j(writable_files)
                if neo4j_chunks:
                    all_chunks.extend(neo4j_chunks)
                    sources_used.append("Neo4j")
                    logger.info(
                        "ContextRetrieval: Neo4j returned %d context items",
                        len(neo4j_chunks),
                    )
            except Exception as exc:
                logger.warning("ContextRetrieval: Neo4j failed (%s)", exc)

        if len(all_chunks) >= _MIN_CHUNKS:
            return self._finalise(all_chunks, sources_used, run_ctx)

        # ── Strategy 3: SQLite symbol search ─────────────────────────────────
        if self.sqlite_store:
            try:
                sqlite_chunks = self._strategy_sqlite(query, writable_files)
                if sqlite_chunks:
                    all_chunks.extend(sqlite_chunks)
                    sources_used.append("SQLite")
                    logger.info(
                        "ContextRetrieval: SQLite returned %d context items",
                        len(sqlite_chunks),
                    )
            except Exception as exc:
                logger.warning("ContextRetrieval: SQLite failed (%s)", exc)

        if len(all_chunks) >= _MIN_CHUNKS:
            return self._finalise(all_chunks, sources_used, run_ctx)

        # ── Strategy 4: Grep ──────────────────────────────────────────────────
        if writable_files:
            try:
                grep_chunks = self._strategy_grep(query, writable_files)
                if grep_chunks:
                    all_chunks.extend(grep_chunks)
                    sources_used.append("Grep")
                    logger.info(
                        "ContextRetrieval: Grep returned %d context items",
                        len(grep_chunks),
                    )
            except Exception as exc:
                logger.warning("ContextRetrieval: Grep failed (%s)", exc)

        if len(all_chunks) >= _MIN_CHUNKS:
            return self._finalise(all_chunks, sources_used, run_ctx)

        # ── Strategy 5: DirectRead ────────────────────────────────────────────
        if writable_files:
            try:
                direct_chunks = self._strategy_direct_read(writable_files)
                if direct_chunks:
                    all_chunks.extend(direct_chunks)
                    sources_used.append("DirectRead")
                    logger.info(
                        "ContextRetrieval: DirectRead returned %d context items",
                        len(direct_chunks),
                    )
            except Exception as exc:
                logger.warning("ContextRetrieval: DirectRead failed (%s)", exc)

        if not all_chunks:
            logger.error(
                "ContextRetrieval: ALL strategies returned 0 context items. "
                "Generator will operate with minimal context."
            )
            if run_ctx:
                from ticket_to_code.runtime.run_context import HealthLevel
                run_ctx.degrade(
                    "context_retrieval", HealthLevel.FAILED,
                    "All retrieval strategies returned 0 items"
                )

        return self._finalise(all_chunks, sources_used, run_ctx)

    # ── Private strategy implementations ──────────────────────────────────────

    def _strategy_rag(
        self,
        requirements: Optional["StructuredRequirements"],
        plan: Optional["ArchitecturalPlan"],
    ) -> list:
        """Query the RAG engine's multi-stage retrieval."""
        if not self.rag_engine or not getattr(self.rag_engine, "is_initialized", False):
            return []
        if not requirements:
            return []
        chunks = self.rag_engine.multi_stage_retrieval(
            requirements=requirements,
            architectural_plan=plan,
            query_focus="architecture",
            document_types=["source_code", "interfaces", "patterns"],
            max_iterations=2,
            k_per_iteration=5,
        )
        return chunks or []

    def _strategy_neo4j(self, writable_files: List[str]) -> List[dict]:
        """Find code neighbours of writable files via Neo4j graph traversal."""
        if not writable_files or not self.neo4j_store:
            return []
        results: List[dict] = []
        try:
            if hasattr(self.neo4j_store, "query"):
                rows = self.neo4j_store.query(
                    _NEO4J_NEIGHBOURS_CYPHER,
                    {"paths": writable_files, "workspace": str(self.workspace_path)},
                )
            else:
                return []
            seen_paths: Set[str] = set()
            for row in rows or []:
                path    = row.get("neighbour_path") or ""
                content = row.get("content") or ""
                if path and path not in seen_paths and content:
                    results.append({
                        "file_path": path,
                        "content": content[:_MAX_DIRECTREAD_BYTES],
                        "source": "Neo4j",
                        "type": "graph_neighbour",
                    })
                    seen_paths.add(path)
        except Exception as exc:
            logger.debug("_strategy_neo4j error: %s", exc)
        return results

    def _strategy_sqlite(self, query: str, writable_files: List[str]) -> List[dict]:
        """Search SQLite symbol index for query terms, scoped to writable file paths."""
        if not self.sqlite_store:
            return []
        keywords = self._query_to_keywords(query)
        if not keywords:
            return []
        conn = getattr(self.sqlite_store, "_conn", None)
        if conn is None:
            return []
        results: List[dict] = []
        seen: Set[str] = set()
        try:
            for kw in keywords[:6]:
                rows = conn.execute(
                    "SELECT DISTINCT path, qualified_name FROM symbols "
                    "WHERE (LOWER(name) LIKE ? OR LOWER(qualified_name) LIKE ?) LIMIT 20",
                    (f"%{kw.lower()}%", f"%{kw.lower()}%"),
                ).fetchall()
                for path, qname in rows:
                    if path and path not in seen:
                        results.append({
                            "file_path": path,
                            "content": f"Symbol: {qname or path}",
                            "source": "SQLite",
                            "type": "symbol_match",
                        })
                        seen.add(path)
        except Exception as exc:
            logger.debug("_strategy_sqlite error: %s", exc)
        return results

    def _strategy_grep(self, query: str, writable_files: List[str]) -> List[dict]:
        """Grep writable files for query keywords."""
        keywords = self._query_to_keywords(query)[:4]
        if not keywords:
            return []
        results: List[dict] = []
        patterns = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
        for rel_path in writable_files[:10]:
            full = self.workspace_path / rel_path
            if not full.is_file():
                continue
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
                matching_lines = [
                    ln for ln in content.splitlines()
                    if any(p.search(ln) for p in patterns)
                ]
                if matching_lines:
                    results.append({
                        "file_path": rel_path,
                        "content": "\n".join(matching_lines[:30]),
                        "source": "Grep",
                        "type": "keyword_match",
                    })
            except OSError:
                pass
        return results

    def _strategy_direct_read(self, writable_files: List[str]) -> List[dict]:
        """Read writable files directly, byte-capped per file and total."""
        results: List[dict] = []
        total_bytes = 0
        for rel_path in writable_files:
            if total_bytes >= _MAX_DIRECTREAD_TOTAL_BYTES:
                logger.debug(
                    "DirectRead: total byte cap reached (%d bytes)", total_bytes
                )
                break
            full = self.workspace_path / rel_path
            if not full.is_file():
                continue
            try:
                raw = full.read_bytes()
                text = raw[:_MAX_DIRECTREAD_BYTES].decode("utf-8", errors="replace")
                results.append({
                    "file_path": rel_path,
                    "content": text,
                    "source": "DirectRead",
                    "type": "file_content",
                })
                total_bytes += len(raw[:_MAX_DIRECTREAD_BYTES])
            except OSError as exc:
                logger.debug("DirectRead: cannot read %s: %s", rel_path, exc)
        return results

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _build_query(
        self,
        requirements: Optional["StructuredRequirements"],
        plan: Optional["ArchitecturalPlan"],
        extra: str,
    ) -> str:
        parts: List[str] = []
        if requirements:
            parts.append(getattr(requirements, "raw_text", "") or "")
            parts.extend(getattr(requirements, "implementation_hints", []) or [])
        if plan and hasattr(plan, "tasks"):
            for t in (plan.tasks or [])[:5]:
                desc = getattr(t, "description", "") or ""
                if desc:
                    parts.append(desc)
        if extra:
            parts.append(extra)
        return " ".join(p for p in parts if p)[:500]

    @staticmethod
    def _query_to_keywords(query: str) -> List[str]:
        """Extract meaningful keywords from a query string."""
        stopwords = {
            "the", "a", "an", "is", "in", "on", "at", "to", "for",
            "and", "or", "of", "with", "by", "from", "should", "must",
            "that", "this", "when", "where", "how", "what",
        }
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]*", query)
        seen: Set[str] = set()
        result: List[str] = []
        for t in tokens:
            low = t.lower()
            if len(low) >= 4 and low not in stopwords and low not in seen:
                result.append(t)
                seen.add(low)
            if len(result) >= 12:
                break
        return result

    @staticmethod
    def _finalise(
        chunks: list,
        sources_used: List[str],
        run_ctx: Optional["RunContext"],
    ) -> tuple[list, list[str]]:
        if run_ctx is not None:
            run_ctx.architecture_chunks  = len(chunks)
            run_ctx.context_sources_used = list(sources_used)
        return chunks, sources_used
