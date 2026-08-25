"""Model 1: Planner — reads a ticket and produces a structured execution plan.

Uses the cheap/fast model (gemini-2.5-flash-lite) via LLMRegistry.get_llm(assistant=False).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from aviator_core.ticket_solver.models import (
    AlternativeApproach,
    ExecutionPlan,
    SkillBlockCall,
    SuccessCriterion,
    TicketComplexity,
    TicketInput,
)

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    prompt_path = _PROMPT_DIR / "planner_system.md"
    return prompt_path.read_text(encoding="utf-8")


class Planner:
    """Model 1 in the pipeline — produces an execution plan from a ticket.

    Role-to-model mapping (per Codex caution #2):
        Planner uses the BASE model (cheap) — LLMRegistry.get_llm(assistant=False)
    """

    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.system_prompt = _load_system_prompt()

    def generate_plan(self, ticket: TicketInput) -> tuple[ExecutionPlan, int]:
        """Generate an execution plan for the given ticket.

        Returns:
            (ExecutionPlan, token_count) — the plan and approximate tokens used.
        """
        user_message = self._build_user_message(ticket)

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_message),
        ]

        logger.info("Planner: generating plan for ticket %s", ticket.ticket_id)
        response = self.llm.invoke(messages)
        raw_content = response.content if hasattr(response, "content") else str(response)

        # Estimate tokens (rough: 1 token ≈ 4 chars)
        token_estimate = (len(self.system_prompt) + len(user_message) + len(raw_content)) // 4

        # Parse JSON from response
        plan = self._parse_response(raw_content, ticket)
        logger.info(
            "Planner: plan generated — %d steps, complexity=%s, needs_clarification=%s",
            len(plan.plan), plan.ticket_complexity.value, plan.needs_clarification,
        )
        return plan, token_estimate

    def generate_plan_with_context(
        self,
        ticket: TicketInput,
        clarification_answers: str = "",
        previous_errors: str = "",
    ) -> tuple[ExecutionPlan, int]:
        """Generate a plan with additional context (clarification answers or errors)."""
        user_message = self._build_user_message(ticket)
        if clarification_answers:
            user_message += f"\n\n## Clarification Answers\n{clarification_answers}"
        if previous_errors:
            user_message += f"\n\n## Previous Errors (fix these)\n{previous_errors}"

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_message),
        ]

        response = self.llm.invoke(messages)
        raw_content = response.content if hasattr(response, "content") else str(response)
        token_estimate = (len(self.system_prompt) + len(user_message) + len(raw_content)) // 4

        plan = self._parse_response(raw_content, ticket)
        return plan, token_estimate

    def _build_user_message(self, ticket: TicketInput) -> str:
        parts = [
            f"## Ticket: {ticket.ticket_id}",
            f"**Title:** {ticket.title}",
            f"\n**Description:**\n{ticket.description}",
        ]
        if ticket.acceptance_criteria:
            parts.append("\n**Acceptance Criteria:**")
            for ac in ticket.acceptance_criteria:
                parts.append(f"- {ac}")
        if ticket.labels:
            parts.append(f"\n**Labels:** {', '.join(ticket.labels)}")
        parts.append(f"\n**Priority:** {ticket.priority}")
        return "\n".join(parts)

    def _parse_response(self, raw: str, ticket: TicketInput) -> ExecutionPlan:
        """Parse JSON from LLM response, with fallback for malformed output."""
        # Extract JSON from markdown code blocks if present
        json_str = raw
        if "```json" in raw:
            start = raw.index("```json") + 7
            end = raw.index("```", start)
            json_str = raw[start:end].strip()
        elif "```" in raw:
            start = raw.index("```") + 3
            end = raw.index("```", start)
            json_str = raw[start:end].strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Planner: failed to parse JSON, creating minimal plan")
            return ExecutionPlan(
                ticket_id=ticket.ticket_id,
                understanding=raw[:500],
                assumptions=["Failed to parse structured plan — using raw response"],
                ticket_complexity=TicketComplexity.SINGLE_FILE_FIX,
                plan=[
                    SkillBlockCall(
                        skill="grep_codebase",
                        args={"pattern": ticket.title.split()[0], "services": []},
                    )
                ],
            )

        # Build typed plan from parsed JSON
        return ExecutionPlan(
            ticket_id=data.get("ticket_id", ticket.ticket_id),
            understanding=data.get("understanding", ""),
            assumptions=data.get("assumptions", []),
            needs_clarification=data.get("needs_clarification", False),
            clarification_questions=data.get("clarification_questions", []),
            alternative_approaches=[
                AlternativeApproach(**a) for a in data.get("alternative_approaches", [])
            ],
            recommended_approach=data.get("recommended_approach", 0),
            recommended_approach_reason=data.get("recommended_approach_reason", ""),
            success_criteria=[
                SuccessCriterion(**sc) for sc in data.get("success_criteria", [])
            ],
            ticket_complexity=TicketComplexity(
                data.get("ticket_complexity", "single_file_fix")
            ),
            plan=[
                SkillBlockCall(**step) for step in data.get("plan", [])
            ],
            reasoning_hints=data.get("reasoning_hints", []),
            target_services=data.get("target_services", []),
        )
