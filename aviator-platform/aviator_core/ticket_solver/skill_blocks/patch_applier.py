"""Skill Block: patch_applier — applies patches to workspace files with backup.

After the Reasoner generates patches, this skill applies them to the actual
filesystem so the build runner can verify compilation.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from aviator_core.ticket_solver.skill_blocks import SkillBlock

logger = logging.getLogger(__name__)


class PatchApplierSkill(SkillBlock):
    """Apply code patches to workspace files with automatic backup."""

    name = "apply_patch"
    description = "Apply code patches to files with backup for rollback"

    DEFAULT_WORKSPACE_ROOT = r"C:\CC4E"

    def __init__(self, workspace_root: str = DEFAULT_WORKSPACE_ROOT):
        self.workspace_root = Path(workspace_root).resolve()

    def _validate_path(self, file_path: str) -> Path | None:
        """Validate that a file path is within the workspace root.

        Returns resolved Path if valid, None if rejected.
        """
        try:
            resolved = Path(file_path).resolve()
            # Check that the resolved path is under workspace root
            resolved.relative_to(self.workspace_root)
            return resolved
        except (ValueError, RuntimeError):
            logger.warning(
                "PatchApplier: REJECTED path outside workspace: %s (root: %s)",
                file_path, self.workspace_root,
            )
            return None

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Apply patches to files.

        Args (via kwargs):
            patches: List of Patch model dicts with file_path and hunks
            create_backup: Whether to create .bak files (default: True)
        """
        patches = kwargs.get("patches", [])
        create_backup = kwargs.get("create_backup", True)

        results: list[dict[str, Any]] = []
        applied: list[str] = []
        failed: list[str] = []

        for patch_data in patches:
            file_path = patch_data.get("file_path", "")
            hunks = patch_data.get("hunks", [])
            is_new_file = patch_data.get("is_new_file", False)
            full_content = patch_data.get("full_content")

            if not file_path:
                failed.append("Empty file_path")
                continue

            path = self._validate_path(file_path)
            if not path:
                failed.append(f"Rejected: {file_path} is outside workspace")
                continue

            try:
                if is_new_file and full_content is not None:
                    # Create new file
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(full_content, encoding="utf-8")
                    applied.append(file_path)
                    results.append({
                        "file": file_path,
                        "action": "created",
                        "success": True,
                    })
                    logger.info("PatchApplier: created new file %s", file_path)

                elif path.exists() and hunks:
                    # Apply hunks to existing file
                    original = path.read_text(encoding="utf-8")

                    if create_backup:
                        backup_path = path.with_suffix(path.suffix + ".bak")
                        shutil.copy2(path, backup_path)

                    modified = self._apply_hunks(original, hunks)

                    if modified != original:
                        path.write_text(modified, encoding="utf-8")
                        applied.append(file_path)
                        results.append({
                            "file": file_path,
                            "action": "modified",
                            "success": True,
                            "hunks_applied": len(hunks),
                        })
                        logger.info(
                            "PatchApplier: modified %s (%d hunks)",
                            file_path, len(hunks),
                        )
                    else:
                        results.append({
                            "file": file_path,
                            "action": "no_change",
                            "success": True,
                            "reason": "Patch content matches existing file",
                        })

                else:
                    msg = f"File not found: {file_path}" if not path.exists() else "No hunks provided"
                    failed.append(f"{file_path}: {msg}")
                    results.append({
                        "file": file_path,
                        "action": "failed",
                        "success": False,
                        "reason": msg,
                    })

            except Exception as e:
                failed.append(f"{file_path}: {e}")
                results.append({
                    "file": file_path,
                    "action": "error",
                    "success": False,
                    "reason": str(e),
                })
                logger.error("PatchApplier: failed on %s: %s", file_path, e)

        return {
            "results": results,
            "applied": applied,
            "failed": failed,
            "total_applied": len(applied),
            "total_failed": len(failed),
        }

    def rollback(self, **kwargs: Any) -> dict[str, Any]:
        """Rollback patches by restoring .bak files.

        Args (via kwargs):
            files: List of file paths to rollback
        """
        files = kwargs.get("files", [])
        restored: list[str] = []

        for file_path in files:
            path = Path(file_path)
            backup = path.with_suffix(path.suffix + ".bak")
            if backup.exists():
                shutil.copy2(backup, path)
                backup.unlink()
                restored.append(file_path)
                logger.info("PatchApplier: rolled back %s", file_path)

        return {"restored": restored, "total": len(restored)}

    @staticmethod
    def _apply_hunks(original: str, hunks: list[dict[str, Any]]) -> str:
        """Apply hunks to file content using exact string matching.

        Each hunk has 'original' (text to find) and 'modified' (replacement).
        """
        content = original

        # Sort hunks by start_line descending so line numbers don't shift
        sorted_hunks = sorted(hunks, key=lambda h: h.get("start_line", 0), reverse=True)

        for hunk in sorted_hunks:
            target = hunk.get("original", "")
            replacement = hunk.get("modified", "")

            if not target:
                continue

            # Try exact match first
            if target in content:
                content = content.replace(target, replacement, 1)
            else:
                # Try with normalized whitespace (strip trailing spaces per line)
                target_stripped = "\n".join(l.rstrip() for l in target.splitlines())
                content_stripped_lines = content.splitlines()
                content_for_match = "\n".join(l.rstrip() for l in content_stripped_lines)

                if target_stripped in content_for_match:
                    # Find the position and do line-based replacement
                    start_idx = content_for_match.index(target_stripped)
                    pre = content_for_match[:start_idx]
                    post = content_for_match[start_idx + len(target_stripped):]
                    content = pre + replacement + post
                else:
                    logger.warning(
                        "PatchApplier: hunk target not found (first 60 chars): '%s'",
                        target[:60],
                    )

        return content
