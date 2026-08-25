from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from aviator_core.ticket_solver.skill_blocks import SkillBlock

logger = logging.getLogger(__name__)


class PomParserSkill(SkillBlock):
    """Extract version and dependency information from Maven pom.xml files."""

    name = "get_pom_version"
    description = "Extract the current version of a service from its pom.xml"

    DEFAULT_WORKSPACE_ROOT = r"C:\CC4E"

    def __init__(self, workspace_root: str = DEFAULT_WORKSPACE_ROOT):
        self.workspace_root = Path(workspace_root).resolve()

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Parse pom.xml.

        Args:
            service (str): Microservice name
        """
        service = kwargs.get("service", "")
        if not service:
            return {"error": "Missing 'service' argument"}

        pom_path = self.workspace_root / service / "pom.xml"
        if not pom_path.exists():
            # Try root pom
            if service == "root":
                pom_path = self.workspace_root / "pom.xml"
            
            if not pom_path.exists():
                return {"error": f"pom.xml not found at {pom_path}"}

        try:
            tree = ET.parse(pom_path)
            root = tree.getroot()
            
            # Maven pom XML usually has a namespace, e.g., xmlns="http://maven.apache.org/POM/4.0.0"
            # We strip namespaces for easier searching
            ns_map = {}
            match = re.match(r'\{.*\}', root.tag)
            ns = match.group(0) if match else ''

            version_node = root.find(f"{ns}version")
            if version_node is None:
                # Look in parent
                parent = root.find(f"{ns}parent")
                if parent is not None:
                    version_node = parent.find(f"{ns}version")
            
            artifact_node = root.find(f"{ns}artifactId")
            
            version = version_node.text if version_node is not None else "unknown"
            artifact_id = artifact_node.text if artifact_node is not None else service
            
            return {
                "service": service,
                "artifact_id": artifact_id,
                "version": version,
                "file": str(pom_path)
            }
        except Exception as e:
            logger.error("Failed to parse pom.xml: %s", e)
            return {"error": f"XML parse error: {e}"}
