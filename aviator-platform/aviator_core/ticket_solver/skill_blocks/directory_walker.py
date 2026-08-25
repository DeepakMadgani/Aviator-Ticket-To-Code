from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aviator_core.ticket_solver.skill_blocks import SkillBlock

logger = logging.getLogger(__name__)


class DirectoryWalkerSkill(SkillBlock):
    """List files in a given microservice."""

    name = "list_service_files"
    description = "List the files and directories inside a microservice, optionally filtered by extension."

    DEFAULT_WORKSPACE_ROOT = r"C:\CC4E"

    def __init__(self, workspace_root: str = DEFAULT_WORKSPACE_ROOT):
        self.workspace_root = Path(workspace_root).resolve()

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute directory walk.

        Args:
            service (str): Name of the microservice. If empty, starts at root.
            extension (str, optional): Only list files ending with this extension (e.g., '.java')
            max_results (int, optional): Cap the number of results. Default 200.
        """
        service = kwargs.get("service", "")
        extension = kwargs.get("extension", "")
        max_results = kwargs.get("max_results", 200)

        target_dir = self.workspace_root
        if service:
            target_dir = target_dir / service

        if not target_dir.exists() or not target_dir.is_dir():
            return {"error": f"Directory not found: {target_dir}"}

        # Resolve to ensure it stays within workspace
        target_dir = target_dir.resolve()
        try:
            target_dir.relative_to(self.workspace_root)
        except ValueError:
            return {"error": "Target directory is outside the workspace root."}

        files = []
        directories = []

        try:
            for path in target_dir.rglob("*"):
                # Skip target/build/node_modules/.git folders
                if any(part in [".git", "target", "build", "node_modules", "dist"] for part in path.parts):
                    continue

                if path.is_file():
                    if extension and not path.name.endswith(extension):
                        continue
                    files.append(str(path.relative_to(self.workspace_root)))
                elif path.is_dir() and not extension:
                    # Only collect directories if not filtering by extension
                    directories.append(str(path.relative_to(self.workspace_root)))

                if len(files) + len(directories) >= max_results:
                    break

            return {
                "service": service,
                "directories": sorted(directories),
                "files": sorted(files),
                "truncated": len(files) + len(directories) >= max_results
            }
        except Exception as e:
            logger.error("Failed to walk directory: %s", e)
            return {"error": str(e)}
