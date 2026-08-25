"""
Phase 3A — Live Repository Search Engine

Filesystem-based, index-independent search across ALL repository file types.

Discovers files that are absent from SQLite, Neo4j, and semantic embeddings
because they were never crawled (e.g. .sh, .bat, .ps1, .json, .xml, .env).

Search modes:
  1. Literal   — exact string in file content  (grep-style, per-line)
  2. Filename  — fragment in repo-relative path (name + directory segments)
  3. Regex     — compiled regex match in file content

Rules:
  • Read-only — never writes to the repository
  • No dependency on SQLite, Neo4j, or embeddings
  • Deduplicates by file path per query
  • Writes a JSON trace: <workspace>/.aviator/repository_search.json

Author: Deepak Madgani
Date: June 2026
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Set

from ticket_to_code.agents.query_expansion import QueryExpansionEngine

logger = logging.getLogger(__name__)


# ── Supported file extensions ─────────────────────────────────────────────────
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".java", ".ts", ".tsx", ".html", ".scss", ".css",
    ".json", ".yml", ".yaml", ".properties", ".env",
    ".sh", ".bat", ".ps1", ".xml",
})

# ── Directories pruned from every walk — never contain indexable code ────────
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode",
    "target", "build", "out", "bin", "dist",
    "node_modules", ".gradle", ".mvn",
    "generated", "generated-sources", "generated-test-sources",
    "__pycache__", ".angular", ".aviator",
    "coverage", "logs", "log", ".nyc_output",
    "postman", "traces", "trace", "benchmark_reports_wrapper",
})

# ── Safety limits ─────────────────────────────────────────────────────────────
_MAX_FILE_BYTES = 2 * 1024 * 1024   # 2 MB — skip files larger than this
_MIN_LITERAL_LEN = 4                 # Allows "26.2", version numbers

# ── Confidence by file type (content match) ───────────────────────────────────
_SCORE_MAP: dict[str, float] = {
    ".java":       0.85,
    ".ts":         0.85,
    ".tsx":        0.85,
    ".sh":         0.82,
    ".bat":        0.82,
    ".ps1":        0.82,
    ".yml":        0.80,
    ".yaml":       0.80,
    ".properties": 0.80,
    ".env":        0.80,
    ".xml":        0.78,
    ".json":       0.75,
    ".html":       0.75,
    ".scss":       0.75,
    ".css":        0.75,
}

# Filename (path) match confidence — always strong signal
_FILENAME_SCORE = 0.88


@dataclass
class SearchResult:
    """One match from the live repository search layer."""

    query: str          # the exact term that matched (may be an expanded form)
    query_type: str     # "literal" | "filename" | "regex"
    file_path: str      # repo-relative POSIX path
    line_number: int    # 1-based line in file; 0 for filename matches
    matched_text: str   # the matching line or path
    confidence: float   # 0.0 – 1.0
    original_literal: str = ""  # the hypothesis literal that triggered expansion


class RepositorySearchEngine:
    """
    Phase 3A: Live, index-independent search across all repository file types.

    Complements indexed knowledge (SQLite / Neo4j / pgvector) by discovering
    files that exist in the repository but were never crawled:

      • Shell scripts        (.sh, .bat, .ps1)
      • JSON data            (.json)
      • XML config           (.xml)
      • Environment files    (.env)
      • All YAML             (.yml, .yaml)
      • Any other supported extension

    Usage::

        engine = RepositorySearchEngine(workspace_path)
        hits = engine.search_literal("26.2")
        hits += engine.search_filename("run-job")
        engine.flush_trace()   # writes .aviator/repository_search.json
    """

    def __init__(
        self,
        workspace_path: Path,
        trace_output: Optional[Path] = None,
        query_expansion_trace: Optional[Path] = None,
    ) -> None:
        self.workspace = Path(workspace_path)
        self.trace_output = trace_output or (
            self.workspace / ".aviator" / "repository_search.json"
        )
        self.query_expansion_trace = query_expansion_trace or (
            self.workspace / ".aviator" / "query_expansion.json"
        )
        self._trace_records: List[dict] = []
        self._query_expansion_records: List[dict] = []
        self.expansion_engine = QueryExpansionEngine()
        # Full provenance map: original_literal → set of expanded forms
        # Populated as search_literal() calls are made; consumed by ranking.
        self._expansion_map: dict = {}   # Dict[str, Set[str]]

    # =========================================================================
    # PUBLIC SEARCH API
    # =========================================================================

    def search_literal(
        self,
        literal: str,
        *,
        extensions: Optional[Set[str]] = None,
        case_sensitive: bool = False,
        max_results: int = 30,
    ) -> List[SearchResult]:
        """
        Search every repository file for an exact string literal,
        automatically applying query expansion for versions and naming conventions.

        Args:
            literal:        String to search for in file content.
            extensions:     Restrict to these extensions; None → all supported.
            case_sensitive: Default False (version strings vary in casing).
            max_results:    Cap per query to avoid flooding evidence list.

        Returns:
            One SearchResult per matching file (first match per file).
        """
        stripped = literal.strip()
        if len(stripped) < _MIN_LITERAL_LEN:
            logger.debug(
                f"[RepoSearch] Skipping '{literal}' — shorter than {_MIN_LITERAL_LEN} chars"
            )
            return []

        expanded_terms = self.expansion_engine.expand(stripped)

        # Persist provenance: original → all expansions
        self._expansion_map[stripped] = expanded_terms

        self._query_expansion_records.append({
            "original": stripped,
            "expanded": sorted(expanded_terms),
        })

        exts   = extensions or SUPPORTED_EXTENSIONS
        results: List[SearchResult] = []
        seen:    Set[str] = set()
        
        # Dynamic Specificity Threshold (Agentic Search Abort)
        # We calculate entropy to avoid falsely aborting on legitimate broad changes like version bumps.
        import re
        if re.search(r'\d+\.\d+', stripped):
            specificity_threshold = max(100, max_results)  # Version numbers are high entropy, allow broad sweep
        elif len(stripped) > 15:
            specificity_threshold = max(50, max_results)   # Long phrases are highly specific
        else:
            specificity_threshold = max_results            # Generic short words get strict abort

        for term in expanded_terms:
            target = term if case_sensitive else term.lower()
            for abs_path, rel_path in self._walk(exts):
                if len(results) > specificity_threshold:
                    # Rank-and-keep instead of discarding everything.
                    # Returning [] silently erased legitimate evidence — e.g. the
                    # .scss / component files that genuinely contain a common UI
                    # label like "Add" or "Cancel". Keep the highest-confidence
                    # hits and let the downstream EvidenceRankingEngine apply its
                    # existing _PENALTY_BROAD_REPO / _PENALTY_WEAK de-noising.
                    results.sort(key=lambda r: r.confidence, reverse=True)
                    kept = results[:max_results]
                    logger.warning(
                        f"[RepoSearch] ⚖️  CAP: Literal '{stripped}' is broad "
                        f"(exceeded {specificity_threshold} matches). "
                        f"Keeping top {len(kept)} by confidence; ranking engine will de-noise."
                    )
                    return kept
                if rel_path in seen:
                    continue
                line_no, line_text = self._grep_first(abs_path, target, case_sensitive)
                if line_no == 0:
                    continue
                score = _SCORE_MAP.get(abs_path.suffix.lower(), 0.75)
                r = SearchResult(
                    query=term,
                    query_type="literal",
                    file_path=rel_path,
                    line_number=line_no,
                    matched_text=line_text[:200],
                    confidence=score,
                    original_literal=stripped,   # provenance tag
                )
                results.append(r)
                seen.add(rel_path)
                self._append_trace(r)

                # Trace result for query_expansion
                self._query_expansion_records.append({
                    "query": term,
                    "original_literal": stripped,
                    "matched_file": rel_path,
                    "source": "query_expansion",
                })

        logger.debug(f"[RepoSearch] literal '{literal}' → {len(results)} file(s)")
        return results

    def search_filename(
        self,
        name_fragment: str,
        *,
        extensions: Optional[Set[str]] = None,
        max_results: int = 20,
    ) -> List[SearchResult]:
        """
        Find files whose repo-relative POSIX path contains `name_fragment`.

        Case-insensitive. Checks the full path (directory segments + filename)
        so 'helm' matches 'project-service/helm/static/run-job.sh'.

        Args:
            name_fragment:  Substring to match anywhere in the path.
            extensions:     Restrict to these extensions; None → all supported.
            max_results:    Cap.

        Returns:
            One SearchResult per matching file (line_number=0).
        """
        if not name_fragment.strip():
            return []
        fragment_lower = name_fragment.strip().lower()
        exts = extensions or SUPPORTED_EXTENSIONS
        results: List[SearchResult] = []

        for _, rel_path in self._walk(exts):
            if len(results) >= max_results:
                break
            if fragment_lower in rel_path.lower():
                r = SearchResult(
                    query=name_fragment,
                    query_type="filename",
                    file_path=rel_path,
                    line_number=0,
                    matched_text=rel_path,
                    confidence=_FILENAME_SCORE,
                )
                results.append(r)
                self._append_trace(r)

        logger.debug(f"[RepoSearch] filename '{name_fragment}' → {len(results)} file(s)")
        return results

    def search_regex(
        self,
        pattern: str,
        *,
        extensions: Optional[Set[str]] = None,
        flags: int = re.IGNORECASE,
        max_results: int = 20,
    ) -> List[SearchResult]:
        """
        Search repository files with a compiled regex.

        Args:
            pattern:    Regex string.
            extensions: Restrict; None → all supported.
            flags:      re module flags.
            max_results: Cap.

        Returns:
            One SearchResult per matching file (first match).
        """
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            logger.warning(f"[RepoSearch] Invalid regex '{pattern}': {exc}")
            return []

        exts = extensions or SUPPORTED_EXTENSIONS
        results: List[SearchResult] = []
        seen: Set[str] = set()

        for abs_path, rel_path in self._walk(exts):
            if len(results) >= max_results or rel_path in seen:
                break
            try:
                if abs_path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = abs_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            m = compiled.search(text)
            if not m:
                continue
            line_no = text[: m.start()].count("\n") + 1
            lines = text.splitlines()
            line_text = lines[line_no - 1].strip() if line_no <= len(lines) else m.group(0)
            score = _SCORE_MAP.get(abs_path.suffix.lower(), 0.75)
            r = SearchResult(
                query=pattern,
                query_type="regex",
                file_path=rel_path,
                line_number=line_no,
                matched_text=line_text[:200],
                confidence=score,
            )
            results.append(r)
            seen.add(rel_path)
            self._append_trace(r)

        logger.debug(f"[RepoSearch] regex '{pattern}' → {len(results)} file(s)")
        return results

    @property
    def expansion_map(self) -> dict:
        """
        Return the accumulated expansion provenance map.

        Dict[str, Set[str]]: maps each original hypothesis literal to the full
        set of expanded forms that were used for search.  Call after all
        search_literal() calls have been made (before flush_trace()).

        This is passed to EvidenceRankingEngine so it can credit files
        discovered via expansion with the same literal-match boost as
        files discovered via the original term.
        """
        return dict(self._expansion_map)

    def flush_trace(self) -> None:
        """
        Write accumulated trace records to `trace_output` (JSON array).
        Also writes query expansion trace.

        Overwrites the file with the current batch so each run reflects only
        the most recent search, preventing unbounded historical accumulation.
        """
        if self._trace_records:
            self.trace_output.parent.mkdir(parents=True, exist_ok=True)
            self.trace_output.write_text(
                json.dumps(self._trace_records, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(
                f"[RepoSearch] trace flushed → {self.trace_output} "
                f"({len(self._trace_records)} records)"
            )
            self._trace_records.clear()

        if self._query_expansion_records:
            self.query_expansion_trace.parent.mkdir(parents=True, exist_ok=True)
            self.query_expansion_trace.write_text(
                json.dumps(self._query_expansion_records, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(
                f"[RepoSearch] query expansion trace flushed → {self.query_expansion_trace} "
                f"({len(self._query_expansion_records)} records)"
            )
            self._query_expansion_records.clear()

    # =========================================================================
    # PRIVATE HELPERS
    # =========================================================================

    def _walk(self, extensions: Set[str]):
        """Yield (abs_path, rel_path) for all matching files under workspace."""
        for root, dirs, files in os.walk(str(self.workspace)):
            # Prune in-place so os.walk skips them entirely
            dirs[:] = [
                d for d in dirs
                if d not in _SKIP_DIRS and not d.startswith(".")
                # allow .aviator? no — skip hidden dirs except ones we explicitly allow
            ]
            root_path = Path(root)
            for fname in sorted(files):           # sorted → deterministic order
                fpath = root_path / fname
                if fpath.suffix.lower() not in extensions:
                    continue
                try:
                    rel = str(fpath.relative_to(self.workspace)).replace("\\", "/")
                except ValueError:
                    continue
                yield fpath, rel

    def _grep_first(
        self,
        abs_path: Path,
        target: str,        # already lowercased if case_sensitive=False
        case_sensitive: bool,
    ) -> tuple[int, str]:
        """
        Return (line_number, line_text) of the FIRST line containing `target`.
        Returns (0, '') if not found or file unreadable.
        """
        try:
            if abs_path.stat().st_size > _MAX_FILE_BYTES:
                return 0, ""
            text = abs_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return 0, ""
        for i, line in enumerate(text.splitlines(), start=1):
            haystack = line if case_sensitive else line.lower()
            if target in haystack:
                return i, line.strip()
        return 0, ""

    def _append_trace(self, r: SearchResult) -> None:
        """Buffer one trace record (written to disk on flush_trace())."""
        self._trace_records.append(asdict(r))
