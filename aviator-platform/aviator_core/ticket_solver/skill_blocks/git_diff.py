from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from aviator_core.ticket_solver.skill_blocks import SkillBlock

logger = logging.getLogger(__name__)


class GitDiffSkill(SkillBlock):
    """Get the diff of recently changed files."""

    name = "diff_since_branch"
    description = "Get git diff of changes made in the current branch or since a commit"

    DEFAULT_WORKSPACE_ROOT = r"C:\CC4E"

    def __init__(self, workspace_root: str = DEFAULT_WORKSPACE_ROOT):
        self.workspace_root = Path(workspace_root).resolve()

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute git diff.

        Args:
            base_ref (str, optional): Branch or commit to compare against. Defaults to "HEAD".
            file_path (str, optional): Specific file to diff.
        """
        base_ref = kwargs.get("base_ref", "HEAD")
        file_path = kwargs.get("file_path")

        cmd = ["git", "diff", base_ref]
        if file_path:
            cmd.extend(["--", file_path])

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                return {"error": f"Git diff failed: {result.stderr}"}

            diff_text = result.stdout
            if not diff_text.strip():
                return {"diff": "No changes found"}

            return {"diff": diff_text[:5000]}  # Cap output size
        except Exception as e:
            logger.error("Failed to run git diff: %s", e)
            return {"error": str(e)}
