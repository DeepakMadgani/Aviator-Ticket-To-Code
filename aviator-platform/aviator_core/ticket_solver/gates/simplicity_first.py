"""Karpathy Principle 2: Simplicity First — Catches overengineering."""

from __future__ import annotations

from typing import Any

from aviator_core.ticket_solver.gates import BaseGate, GateResult
from aviator_core.ticket_solver.models import Patch, TicketComplexity


class SimplicityGate(BaseGate):
    """Catches patches that are overcomplicated for the ticket's complexity.

    Uses adaptive thresholds with per-service multipliers.
    """

    gate_name = "simplicity_first"

    # Base thresholds (lines changed) — will be calibrated from 20-ticket run
    BASE_THRESHOLDS = {
        TicketComplexity.CONFIG_CHANGE: 20,
        TicketComplexity.SINGLE_FILE_FIX: 80,
        TicketComplexity.MULTI_FILE_FIX: 200,
        TicketComplexity.UI_FIX: 250,
        TicketComplexity.FEATURE: 500,
    }

    # Service multipliers — some services are more verbose by nature
    SERVICE_MULTIPLIERS = {
        "xchange-ui": 1.5,       # TypeScript/React tends to be verbose
        "area-service": 1.0,     # Java baseline
        "sagas-service": 1.2,    # Saga handlers are larger
        "project-service": 1.0,
        "deliverable-service": 1.0,
        "gateway-service": 0.8,  # Gateway changes should be small
    }

    def validate(
        self,
        patches: list[Patch],
        ticket_complexity: TicketComplexity,
        primary_service: str = "",
    ) -> GateResult:
        """Check if the patch size is proportional to ticket complexity."""
        total_lines = sum(p.lines_added + p.lines_removed for p in patches)
        multiplier = self.SERVICE_MULTIPLIERS.get(primary_service, 1.0)
        threshold = self.BASE_THRESHOLDS.get(ticket_complexity, 200) * multiplier

        issues: list[str] = []

        if total_lines > threshold:
            issues.append(
                f"Patch changes {total_lines} lines for a '{ticket_complexity.value}' ticket "
                f"(threshold: {threshold:.0f} for {primary_service or 'unknown service'}). "
                f"Is this overcomplicated?"
            )

        # Check for new files that shouldn't exist for simple fixes
        new_files = [p for p in patches if p.is_new_file]
        if new_files and ticket_complexity in (
            TicketComplexity.CONFIG_CHANGE,
            TicketComplexity.SINGLE_FILE_FIX,
        ):
            issues.append(
                f"Creating {len(new_files)} new file(s) for a {ticket_complexity.value}. "
                f"Are these really necessary?"
            )

        # Check for new abstractions (classes/interfaces) in non-feature tickets
        if ticket_complexity != TicketComplexity.FEATURE:
            for p in patches:
                if p.full_content:
                    content_lower = p.full_content.lower()
                    if "abstract class" in content_lower or "interface " in content_lower:
                        issues.append(
                            f"New abstraction in {p.file_path}. Was this requested?"
                        )

        return self._make_result(
            issues,
            total_lines_changed=total_lines,
            threshold=threshold,
            files_modified=len(patches),
            new_files=len(new_files),
        )
