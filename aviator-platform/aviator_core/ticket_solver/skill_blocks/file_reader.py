"""Skill Block: file_reader — reads specific line ranges from files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aviator_core.ticket_solver.skill_blocks import SkillBlock

logger = logging.getLogger(__name__)


class FileReaderSkill(SkillBlock):
    """Read specific line ranges from files — focused, not whole files."""

    name = "read_file_range"
    description = "Read specific lines from a file (not the whole file)"

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Read file content, optionally limited to a line range.

        Args (via kwargs):
            file_path: Absolute path to the file
            start_line: First line to read (1-indexed, default: 1)
            end_line: Last line to read (0 = end of file)
            context_lines: Number of context lines around a match
        """
        file_path = kwargs.get("file_path", "")
        start_line = kwargs.get("start_line", 1)
        end_line = kwargs.get("end_line", 0)
        context_lines = kwargs.get("context_lines", 0)

        if not file_path:
            return {"files": [], "error": "No file_path provided"}

        path = Path(file_path)
        if not path.exists():
            return {"files": [], "error": f"File not found: {file_path}"}

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            total_lines = len(lines)

            # Apply line range
            actual_start = max(1, start_line - context_lines)
            actual_end = min(total_lines, (end_line or total_lines) + context_lines)

            selected_lines = lines[actual_start - 1 : actual_end]
            selected_content = "\n".join(selected_lines)

            file_entry = {
                "path": str(path),
                "content": selected_content,
                "start_line": actual_start,
                "end_line": actual_end,
                "is_full_file": actual_start == 1 and actual_end == total_lines,
                "total_lines": total_lines,
            }

            logger.info(
                "read_file_range: %s lines %d-%d (total %d)",
                path.name, actual_start, actual_end, total_lines,
            )
            return {"files": [file_entry]}

        except Exception as e:
            logger.error("read_file_range failed for %s: %s", file_path, e)
            return {"files": [], "error": str(e)}


class BatchFileReaderSkill(SkillBlock):
    """Read multiple files, each limited to context_lines around grep matches."""

    name = "read_matched_files"
    description = "Read multiple files at grep match locations"

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Read files at match locations with surrounding context.

        Args (via kwargs):
            matches: List of grep match dicts with 'file' and 'line' keys
            context_lines: Number of lines above/below each match (default: 30)
        """
        matches = kwargs.get("matches", [])
        context_lines = kwargs.get("context_lines", 30)

        reader = FileReaderSkill()
        all_files: list[dict[str, Any]] = []
        seen_ranges: set[str] = set()

        for match in matches:
            file_path = match.get("file", "")
            line = match.get("line", 1)

            # Deduplicate overlapping ranges for same file
            range_key = f"{file_path}:{line // context_lines}"
            if range_key in seen_ranges:
                continue
            seen_ranges.add(range_key)

            result = reader.execute(
                file_path=file_path,
                start_line=line,
                end_line=line,
                context_lines=context_lines,
            )
            all_files.extend(result.get("files", []))

        logger.info(
            "read_matched_files: %d matches → %d unique file ranges",
            len(matches), len(all_files),
        )
        return {"files": all_files}
