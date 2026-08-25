from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import yaml

from aviator_core.ticket_solver.skill_blocks import SkillBlock

logger = logging.getLogger(__name__)


class ConfigReaderSkill(SkillBlock):
    """Read and parse Spring Boot configuration files (.yml, .yaml, .properties)."""

    name = "read_spring_config"
    description = "Read keys and values from Spring application.yml or application.properties"

    DEFAULT_WORKSPACE_ROOT = r"C:\CC4E"

    def __init__(self, workspace_root: str = DEFAULT_WORKSPACE_ROOT):
        self.workspace_root = Path(workspace_root).resolve()

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute config read.

        Args:
            service (str): The name of the microservice (e.g., 'area-service')
            keys (list[str], optional): Specific config keys to extract. If empty, returns all.
        """
        service = kwargs.get("service", "")
        keys = kwargs.get("keys", [])

        if not service:
            return {"error": "Missing 'service' argument"}

        # Look for application.yml or application.properties in standard Spring paths
        base_path = self.workspace_root / service / "src" / "main" / "resources"
        
        if not base_path.exists():
            return {"error": f"Service resources directory not found: {base_path}"}

        config_files = []
        for ext in ["yml", "yaml", "properties"]:
            cf = base_path / f"application.{ext}"
            if cf.exists():
                config_files.append(cf)

        if not config_files:
            return {"error": f"No application.yml/.properties found in {base_path}"}

        results = {}
        for cf in config_files:
            content = cf.read_text(encoding="utf-8")
            if cf.suffix in [".yml", ".yaml"]:
                try:
                    parsed = yaml.safe_load(content)
                    extracted = self._extract_yaml(parsed, keys)
                    results[cf.name] = extracted
                except Exception as e:
                    logger.warning("Failed to parse YAML %s: %s", cf.name, e)
                    results[cf.name] = {"error": f"YAML parse error: {e}"}
            else:
                extracted = self._extract_properties(content, keys)
                results[cf.name] = extracted

        return {
            "service": service,
            "configs": results
        }

    def _extract_yaml(self, data: dict, keys_to_find: list[str]) -> dict:
        """Flatten YAML dict and filter by keys if specified."""
        flat = self._flatten_dict(data)
        if not keys_to_find:
            return flat
        
        filtered = {}
        for target in keys_to_find:
            for k, v in flat.items():
                if target in k:
                    filtered[k] = v
        return filtered

    def _flatten_dict(self, d: dict, parent_key: str = "", sep: str = ".") -> dict:
        items = []
        for k, v in d.items() if d else []:
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)

    def _extract_properties(self, content: str, keys_to_find: list[str]) -> dict:
        """Extract keys from .properties file."""
        props = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
            elif ":" in line:
                k, v = line.split(":", 1)
                props[k.strip()] = v.strip()
                
        if not keys_to_find:
            return props
            
        filtered = {}
        for target in keys_to_find:
            for k, v in props.items():
                if target in k:
                    filtered[k] = v
        return filtered
