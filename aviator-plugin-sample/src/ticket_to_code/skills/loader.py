from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple


DEFAULT_SKILLS: Dict[str, dict] = {
    "grounded-planning": {
        "id": "grounded-planning",
        "title": "Grounded Planning",
        "rules": [
            "Select files from discovered candidates only",
            "Prefer owner files before consumer files",
            "Require evidence-backed reason for each writable task",
        ],
    },
    "minimal-diff": {
        "id": "minimal-diff",
        "title": "Minimal Diff",
        "rules": [
            "Change the fewest files required",
            "Avoid broad refactors unless requested",
            "Preserve existing public APIs unless ticket requires change",
        ],
    },
    "api-contract": {
        "id": "api-contract",
        "title": "API Contract Safety",
        "rules": [
            "Treat request/response models as compatibility boundaries",
            "List impacted consumers in planning notes",
            "Do not break required fields without migration path",
        ],
    },
    "backward-compat": {
        "id": "backward-compat",
        "title": "Backward Compatibility",
        "rules": [
            "Prefer additive changes over breaking changes",
            "Guard behavior behind defaults when possible",
            "Retain old behavior unless acceptance criteria says otherwise",
        ],
    },
    "consumer-impact": {
        "id": "consumer-impact",
        "title": "Consumer Impact Check",
        "rules": [
            "Identify downstream consumers before edits",
            "Mark verification-only files explicitly",
            "Avoid writing consumer files unless owner change is insufficient",
        ],
    },
    "config-migration": {
        "id": "config-migration",
        "title": "Config Migration",
        "rules": [
            "Update source-of-truth config first",
            "Track all config aliases/variants",
            "Validate runtime compatibility after change",
        ],
    },
    "version-propagation": {
        "id": "version-propagation",
        "title": "Version Propagation",
        "rules": [
            "Locate canonical version owner",
            "Separate owner updates from display/consumer verification",
            "Avoid generated or lock files unless explicitly requested",
        ],
    },
    "runtime-compat": {
        "id": "runtime-compat",
        "title": "Runtime Compatibility",
        "rules": [
            "Respect runtime/tooling constraints in workspace",
            "Fail closed when mandatory runtime checks cannot run",
            "Prefer deterministic checks over assumptions",
        ],
    },
    "rename-propagation": {
        "id": "rename-propagation",
        "title": "Rename Propagation",
        "rules": [
            "Update declarations and all references",
            "Include imports/usages in task sequence",
            "Ensure no stale symbol remains after patch",
        ],
    },
    "public-api-safety": {
        "id": "public-api-safety",
        "title": "Public API Safety",
        "rules": [
            "Treat exported interfaces as stable",
            "Document any signature changes",
            "Add compatibility handling where needed",
        ],
    },
    "bug-isolation": {
        "id": "bug-isolation",
        "title": "Bug Isolation",
        "rules": [
            "Constrain edits to root-cause path",
            "Require symptom->cause->fix chain",
            "Avoid opportunistic unrelated cleanup",
        ],
    },
    "regression-guard": {
        "id": "regression-guard",
        "title": "Regression Guard",
        "rules": [
            "Retain behavior outside ticket scope",
            "Prefer explicit validation points",
            "Flag risk areas for verification",
        ],
    },
}


class SkillLoader:
    """Load strategy skills from defaults + optional repo-local overrides."""

    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)

    def load_for_tags(self, tags: List[str], max_skills: int = 6) -> Tuple[List[dict], str]:
        skills: List[dict] = []
        local_index = self._load_local_skill_index()

        for tag in tags:
            if len(skills) >= max_skills:
                break
            skill = local_index.get(tag) or DEFAULT_SKILLS.get(tag)
            if skill:
                skills.append(skill)

        guidance = self._build_guidance(skills)
        return skills, guidance

    def _load_local_skill_index(self) -> Dict[str, dict]:
        index: Dict[str, dict] = {}
        candidates = [
            self.workspace_path / ".aviator" / "skills",
            self.workspace_path / "skills",
        ]
        for skill_dir in candidates:
            if not skill_dir.exists() or not skill_dir.is_dir():
                continue
            for fp in skill_dir.glob("*.json"):
                try:
                    raw = json.loads(fp.read_text(encoding="utf-8"))
                    sid = raw.get("id")
                    if sid:
                        index[str(sid)] = raw
                except Exception:
                    continue
        return index

    def _build_guidance(self, skills: List[dict]) -> str:
        if not skills:
            return "No skills loaded. Use standard grounded planning discipline."

        lines: List[str] = ["=== ACTIVE SKILLS ==="]
        for skill in skills:
            lines.append(f"- {skill.get('id', 'unknown')}: {skill.get('title', 'Untitled')}")
            for rule in skill.get("rules", [])[:4]:
                lines.append(f"  * {rule}")
        lines.append("=== END ACTIVE SKILLS ===")
        return "\n".join(lines)
