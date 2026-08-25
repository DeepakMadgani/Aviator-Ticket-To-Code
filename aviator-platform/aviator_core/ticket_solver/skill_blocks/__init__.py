"""Skill Block registry and base interface."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class SkillBlock(ABC):
    """Base class for all deterministic skill blocks.

    Skill blocks are pure Python — zero LLM tokens. They search, read,
    query, and transform code context.
    """

    name: str = "base_skill"
    description: str = ""

    @abstractmethod
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the skill and return results.

        Returns a dict that can be merged into GatheredContext.
        """
        ...


class SkillRegistry:
    """Registry of available skill blocks."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillBlock] = {}

    def register(self, skill: SkillBlock) -> None:
        self._skills[skill.name] = skill
        logger.info("Registered skill block: %s", skill.name)

    def get(self, name: str) -> SkillBlock | None:
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    @classmethod
    def create_default(cls) -> "SkillRegistry":
        """Create a registry with all built-in skill blocks."""
        from aviator_core.ticket_solver.skill_blocks.build_runner import BuildRunnerSkill
        from aviator_core.ticket_solver.skill_blocks.file_reader import BatchFileReaderSkill, FileReaderSkill
        from aviator_core.ticket_solver.skill_blocks.grep_codebase import GrepCodebaseSkill
        from aviator_core.ticket_solver.skill_blocks.patch_applier import PatchApplierSkill
        from aviator_core.ticket_solver.skill_blocks.config_reader import ConfigReaderSkill
        from aviator_core.ticket_solver.skill_blocks.git_diff import GitDiffSkill
        from aviator_core.ticket_solver.skill_blocks.pom_parser import PomParserSkill
        from aviator_core.ticket_solver.skill_blocks.directory_walker import DirectoryWalkerSkill
        from aviator_core.ticket_solver.skill_blocks.neo4j_query import Neo4jQuerySkill, FindCallersSkill, FindImplementationsSkill

        registry = cls()
        registry.register(GrepCodebaseSkill())
        registry.register(FileReaderSkill())
        registry.register(BatchFileReaderSkill())
        registry.register(BuildRunnerSkill())
        registry.register(PatchApplierSkill())
        registry.register(ConfigReaderSkill())
        registry.register(GitDiffSkill())
        registry.register(PomParserSkill())
        registry.register(DirectoryWalkerSkill())
        registry.register(Neo4jQuerySkill())
        registry.register(FindCallersSkill())
        registry.register(FindImplementationsSkill())
        
        return registry
