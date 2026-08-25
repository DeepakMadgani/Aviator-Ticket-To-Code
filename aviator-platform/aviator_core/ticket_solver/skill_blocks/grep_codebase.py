"""Skill Block: grep_codebase — regex/text search across CC4E microservices."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from aviator_core.ticket_solver.skill_blocks import SkillBlock

logger = logging.getLogger(__name__)

DEFAULT_CC4E_PATH = r"C:\CC4E"


class GrepCodebaseSkill(SkillBlock):
    """Search for patterns across CC4E microservices using ripgrep or fallback."""

    name = "grep_codebase"
    description = "Regex/text search across CC4E microservices"

    def __init__(self, cc4e_path: str = DEFAULT_CC4E_PATH):
        self.cc4e_path = Path(cc4e_path)

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Search for a pattern in specified services.

        Args (via kwargs):
            pattern: Search pattern (string or regex)
            services: List of service directories to search (e.g., ["area-service"])
            file_types: File extensions to include (e.g., ["*.java", "*.ts"])
            is_regex: Whether pattern is a regex (default: False)
            max_results: Maximum number of results (default: 50)
        """
        pattern = kwargs.get("pattern", "")
        services = kwargs.get("services", [])
        file_types = kwargs.get("file_types", ["*.java"])
        is_regex = kwargs.get("is_regex", False)
        max_results = kwargs.get("max_results", 50)

        if not pattern:
            return {"grep_matches": [], "error": "No pattern provided"}

        matches: list[dict[str, Any]] = []

        # Determine search paths
        search_paths: list[Path] = []
        if services:
            for svc in services:
                svc_path = self.cc4e_path / svc
                if svc_path.exists():
                    search_paths.append(svc_path)
                else:
                    logger.warning("Service directory not found: %s", svc_path)
        else:
            search_paths = [self.cc4e_path]

        for search_path in search_paths:
            results = self._search_path(
                search_path, pattern, file_types, is_regex, max_results - len(matches)
            )
            matches.extend(results)
            if len(matches) >= max_results:
                break

        logger.info(
            "grep_codebase: pattern='%s' services=%s found=%d matches",
            pattern, services, len(matches),
        )
        return {"grep_matches": matches[:max_results]}

    def _search_path(
        self,
        path: Path,
        pattern: str,
        file_types: list[str],
        is_regex: bool,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Search a single path using Python regex fallback."""
        matches: list[dict[str, Any]] = []

        for file_type in file_types:
            ext = file_type.lstrip("*")
            for file_path in path.rglob(f"*{ext}"):
                # Skip build output, node_modules, etc.
                parts = file_path.relative_to(self.cc4e_path).parts
                skip_dirs = {"target", "build", "node_modules", ".git", "dist", "out"}
                if any(p in skip_dirs for p in parts):
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    lines = content.splitlines()
                    for i, line in enumerate(lines, 1):
                        if is_regex:
                            if re.search(pattern, line):
                                matches.append(self._make_match(file_path, i, line))
                        else:
                            if pattern.lower() in line.lower():
                                matches.append(self._make_match(file_path, i, line))

                        if len(matches) >= max_results:
                            return matches
                except (OSError, UnicodeDecodeError):
                    continue

        return matches

    def _make_match(self, file_path: Path, line_num: int, line_content: str) -> dict[str, Any]:
        return {
            "file": str(file_path),
            "line": line_num,
            "content": line_content.strip()[:200],  # Truncate long lines
            "relative_path": str(file_path.relative_to(self.cc4e_path)),
        }
