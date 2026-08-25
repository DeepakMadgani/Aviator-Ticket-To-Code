"""Executor — runs Skill Block plans and gathers context. Zero LLM tokens."""

from __future__ import annotations

import logging
from typing import Any

from aviator_core.ticket_solver.budget import TicketBudget
from aviator_core.ticket_solver.models import (
    ExecutionPlan,
    FileContext,
    GatheredContext,
)
from aviator_core.ticket_solver.skill_blocks import SkillRegistry

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 15_000


class Executor:
    """Runs the Planner's skill block plan and assembles focused context.

    This is the 'compression layer' — out of 10,000 files in CC4E,
    only the 5-15 relevant files/methods get passed to the Reasoner.
    """

    def __init__(self, skill_registry: SkillRegistry | None = None):
        self.registry = skill_registry or SkillRegistry.create_default()

    def execute_plan(
        self,
        plan: ExecutionPlan,
        budget: TicketBudget,
    ) -> GatheredContext:
        """Execute each step in the plan and merge results into context."""
        context = GatheredContext()
        step_results: dict[str, Any] = {}  # For inter-step references

        for i, step in enumerate(plan.plan):
            if not budget.can_call_skill():
                logger.warning(
                    "Executor: skill block budget exhausted at step %d/%d",
                    i, len(plan.plan),
                )
                break

            skill = self.registry.get(step.skill)
            if not skill:
                logger.warning("Executor: unknown skill block '%s', skipping.", step.skill)
                continue

            # Resolve inter-step references (e.g., FROM_GREP_RESULTS)
            resolved_args = self._resolve_references(step.args, step_results)

            try:
                result = skill.execute(**resolved_args)
                budget.record_skill_call()
                step_results[f"step_{i}"] = result
                context = self._merge_result(context, result)

                logger.info(
                    "Executor: step %d/%d skill='%s' OK",
                    i + 1, len(plan.plan), step.skill,
                )
            except Exception as e:
                logger.error(
                    "Executor: step %d skill='%s' failed: %s",
                    i, step.skill, e,
                )

        # Compress if context exceeds token budget
        if context.estimate_tokens() > MAX_CONTEXT_TOKENS:
            original_tokens = context.estimate_tokens()
            context = context.compress(MAX_CONTEXT_TOKENS)
            logger.info(
                "Executor: compressed context %d → %d tokens",
                original_tokens, context.estimate_tokens(),
            )

        return context

    def _resolve_references(
        self,
        args: dict[str, Any],
        step_results: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace FROM_GREP_RESULTS etc. with actual step output."""
        resolved = {}
        for key, value in args.items():
            if isinstance(value, str) and value == "FROM_GREP_RESULTS":
                # Collect all grep matches from previous steps
                all_matches: list[dict[str, Any]] = []
                for step_result in step_results.values():
                    all_matches.extend(step_result.get("grep_matches", []))
                resolved["matches"] = all_matches
            elif isinstance(value, list) and "FROM_GREP_RESULTS" in value:
                all_matches = []
                for step_result in step_results.values():
                    all_matches.extend(step_result.get("grep_matches", []))
                resolved["matches"] = all_matches
            else:
                resolved[key] = value
        return resolved

    def _merge_result(
        self,
        context: GatheredContext,
        result: dict[str, Any],
    ) -> GatheredContext:
        """Merge a skill block result into the gathered context."""
        new_files = context.files.copy()
        new_symbols = context.symbols.copy()
        new_grep = context.grep_matches.copy()
        new_deps = context.dependency_chain.copy()

        # Merge files (deduplicate by path)
        existing_paths = {f.path for f in new_files}
        for file_data in result.get("files", []):
            path = file_data.get("path", "")
            if path and path not in existing_paths:
                new_files.append(FileContext(
                    path=path,
                    content=file_data.get("content", ""),
                    start_line=file_data.get("start_line", 1),
                    end_line=file_data.get("end_line", 0),
                    is_full_file=file_data.get("is_full_file", True),
                ))
                existing_paths.add(path)

        # Merge grep matches
        new_grep.extend(result.get("grep_matches", []))

        # Merge symbols
        new_symbols.extend(result.get("symbols", []))

        # Merge dependencies
        new_deps.extend(result.get("dependency_chain", []))

        return GatheredContext(
            files=new_files,
            symbols=new_symbols,
            grep_matches=new_grep,
            dependency_chain=list(set(new_deps)),
        )
