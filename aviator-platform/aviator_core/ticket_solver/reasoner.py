"""Model 2: Reasoner — produces code patches from focused context.

Uses the strong model (gemini-2.5-flash) via LLMRegistry.get_llm(assistant=True).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from aviator_core.ticket_solver.models import (
    CriterionVerification,
    ExecutionPlan,
    GatheredContext,
    Patch,
    PatchHunk,
    ReasonerOutput,
    TicketInput,
)

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    prompt_path = _PROMPT_DIR / "reasoner_system.md"
    return prompt_path.read_text(encoding="utf-8")


class Reasoner:
    """Model 2 in the pipeline — generates code patches from context.

    Role-to-model mapping:
        Reasoner uses the ASSISTANT model (strong) — LLMRegistry.get_llm(assistant=True)
    """

    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.system_prompt = _load_system_prompt()

    def generate_patches(
        self,
        ticket: TicketInput,
        plan: ExecutionPlan,
        context: GatheredContext,
        build_errors: str = "",
    ) -> tuple[ReasonerOutput, int]:
        """Generate code patches for the ticket.

        Args:
            ticket: The original ticket
            plan: The execution plan from the Planner
            context: Gathered code context from the Executor
            build_errors: Optional build errors from a previous attempt

        Returns:
            (ReasonerOutput, token_count)
        """
        user_message = self._build_user_message(ticket, plan, context, build_errors)

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_message),
        ]

        logger.info("Reasoner: generating patches for ticket %s", ticket.ticket_id)
        response = self.llm.invoke(messages)
        raw_content = response.content if hasattr(response, "content") else str(response)

        token_estimate = (len(self.system_prompt) + len(user_message) + len(raw_content)) // 4

        output = self._parse_response(raw_content)
        logger.info(
            "Reasoner: %d patches generated, confidence=%.2f",
            len(output.patches), output.confidence,
        )
        return output, token_estimate

    def _build_user_message(
        self,
        ticket: TicketInput,
        plan: ExecutionPlan,
        context: GatheredContext,
        build_errors: str,
    ) -> str:
        parts: list[str] = []

        # Ticket
        parts.append(f"## Ticket: {ticket.ticket_id}")
        parts.append(f"**Title:** {ticket.title}")
        parts.append(f"**Description:** {ticket.description}")

        # Planner's understanding and hints
        parts.append(f"\n## Planner's Understanding\n{plan.understanding}")
        if plan.assumptions:
            parts.append("\n**Assumptions:**")
            for a in plan.assumptions:
                parts.append(f"- {a}")
        if plan.reasoning_hints:
            parts.append("\n**Reasoning Hints:**")
            for h in plan.reasoning_hints:
                parts.append(f"- {h}")

        # Success criteria to verify
        if plan.success_criteria:
            parts.append("\n## Success Criteria (you MUST verify each one)")
            for sc in plan.success_criteria:
                parts.append(f"- **{sc.id}**: {sc.description} (type: {sc.verification_type})")

        # Code context
        parts.append("\n## Code Context")
        for f in context.files:
            range_info = ""
            if not f.is_full_file:
                range_info = f" (lines {f.start_line}-{f.end_line})"
            parts.append(f"\n### {f.path}{range_info}")
            parts.append(f"```\n{f.content}\n```")

        # Grep matches summary
        if context.grep_matches:
            parts.append(f"\n## Search Results ({len(context.grep_matches)} matches)")
            for m in context.grep_matches[:20]:  # Limit to 20
                parts.append(f"- `{m.get('relative_path', '')}:{m.get('line', '')}` — {m.get('content', '')[:100]}")

        # Build errors (for retry attempts)
        if build_errors:
            parts.append(f"\n## ⚠️ Previous Build Errors (FIX THESE)")
            parts.append(f"```\n{build_errors}\n```")
            parts.append(
                "Fix the errors above while maintaining the original fix intent. "
                "Do NOT revert the original fix — adjust it to compile."
            )

        return "\n".join(parts)

    def _parse_response(self, raw: str) -> ReasonerOutput:
        """Parse JSON from the Reasoner's response."""
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
            logger.warning("Reasoner: failed to parse JSON response")
            return ReasonerOutput(
                root_cause="Failed to parse structured response",
                explanation=raw[:1000],
                confidence=0.0,
            )

        # Parse patches
        patches: list[Patch] = []
        for p_data in data.get("patches", []):
            hunks = [
                PatchHunk(
                    start_line=h.get("start_line", 0),
                    end_line=h.get("end_line", 0),
                    original=h.get("original", ""),
                    modified=h.get("modified", ""),
                )
                for h in p_data.get("hunks", [])
            ]
            patches.append(Patch(
                file_path=p_data.get("file_path", ""),
                hunks=hunks,
                is_new_file=p_data.get("is_new_file", False),
                full_content=p_data.get("full_content"),
            ))

        # Parse verifications
        verifications: dict[str, CriterionVerification] = {}
        for key, v_data in data.get("verifications", {}).items():
            verifications[key] = CriterionVerification(
                criterion_id=v_data.get("criterion_id", key),
                passed=v_data.get("passed", False),
                reason=v_data.get("reason", ""),
            )

        return ReasonerOutput(
            root_cause=data.get("root_cause", ""),
            explanation=data.get("explanation", ""),
            patches=patches,
            verifications=verifications,
            confidence=data.get("confidence", 0.0),
            target_files=data.get("target_files", []),
        )
