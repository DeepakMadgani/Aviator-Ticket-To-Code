"""Model 3: Reporter — generates human-readable walkthrough summaries.

Uses the cheap model (gemini-2.5-flash-lite) via LLMRegistry.get_llm(assistant=False).
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from aviator_core.ticket_solver.models import (
    ExecutionPlan,
    ReasonerOutput,
    TicketInput,
)

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    prompt_path = _PROMPT_DIR / "reporter_system.md"
    return prompt_path.read_text(encoding="utf-8")


class Reporter:
    """Model 3 in the pipeline — generates walkthrough summaries.

    Role-to-model mapping:
        Reporter uses the BASE model (cheap) — LLMRegistry.get_llm(assistant=False)
    """

    def __init__(self, llm: BaseChatModel):
        self.llm = llm
        self.system_prompt = _load_system_prompt()

    def generate_report(
        self,
        ticket: TicketInput,
        plan: ExecutionPlan,
        reasoner_output: ReasonerOutput,
        build_result: str = "Build passed",
    ) -> tuple[str, int]:
        """Generate a walkthrough summary.

        Returns:
            (report_markdown, token_count)
        """
        user_message = self._build_user_message(
            ticket, plan, reasoner_output, build_result
        )

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_message),
        ]

        logger.info("Reporter: generating report for ticket %s", ticket.ticket_id)
        response = self.llm.invoke(messages)
        raw_content = response.content if hasattr(response, "content") else str(response)

        token_estimate = (len(self.system_prompt) + len(user_message) + len(raw_content)) // 4

        return raw_content, token_estimate

    def _build_user_message(
        self,
        ticket: TicketInput,
        plan: ExecutionPlan,
        reasoner_output: ReasonerOutput,
        build_result: str,
    ) -> str:
        parts: list[str] = []

        parts.append(f"## Ticket: {ticket.ticket_id} — {ticket.title}")
        parts.append(f"\n**Description:** {ticket.description[:500]}")

        parts.append(f"\n## Root Cause\n{reasoner_output.root_cause}")
        parts.append(f"\n## Explanation\n{reasoner_output.explanation}")

        parts.append(f"\n## Files Modified ({len(reasoner_output.patches)})")
        for patch in reasoner_output.patches:
            total_hunks = len(patch.hunks)
            parts.append(
                f"- `{patch.file_path}` — {total_hunks} hunk(s), "
                f"+{patch.lines_added}/-{patch.lines_removed} lines"
            )

        parts.append(f"\n## Build Result\n{build_result}")

        if plan.success_criteria:
            parts.append("\n## Success Criteria Verification")
            for sc in plan.success_criteria:
                v = reasoner_output.verifications.get(sc.id)
                status = "✅ PASS" if (v and v.passed) else "❌ FAIL"
                reason = v.reason if v else "Not verified"
                parts.append(f"- **{sc.id}**: {sc.description} — {status} ({reason})")

        parts.append(f"\n## Confidence: {reasoner_output.confidence:.0%}")

        return "\n".join(parts)
