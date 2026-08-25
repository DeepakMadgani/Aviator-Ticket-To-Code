"""Karpathy Principle 4: Goal-Driven Execution — Know when you're done."""

from __future__ import annotations

from typing import Any

from aviator_core.ticket_solver.gates import BaseGate, GateResult
from aviator_core.ticket_solver.models import (
    ExecutionPlan,
    ReasonerOutput,
    TicketComplexity,
)

# Outcome verification templates per ticket class
OUTCOME_TEMPLATES: dict[TicketComplexity, list[dict[str, str]]] = {
    TicketComplexity.CONFIG_CHANGE: [
        {"type": "build", "description": "Project compiles"},
        {"type": "value_check", "description": "Config value matches expected"},
    ],
    TicketComplexity.SINGLE_FILE_FIX: [
        {"type": "build", "description": "Project compiles"},
        {"type": "grep_absent", "description": "Bug pattern no longer present in code"},
        {"type": "grep_present", "description": "Fix pattern is present in code"},
    ],
    TicketComplexity.MULTI_FILE_FIX: [
        {"type": "build", "description": "Project compiles"},
        {"type": "grep_absent", "description": "Bug pattern absent from all target files"},
        {"type": "consistency", "description": "All modified files use same approach"},
    ],
    TicketComplexity.UI_FIX: [
        {"type": "build", "description": "npm run build passes"},
        {"type": "grep_present", "description": "CSS/component fix is present"},
        {"type": "no_regressions", "description": "No other components reference changed selectors"},
    ],
    TicketComplexity.FEATURE: [
        {"type": "build", "description": "Project compiles"},
        {"type": "grep_present", "description": "New feature code exists"},
        {"type": "integration", "description": "Feature integrates with existing code"},
    ],
}


class GoalDrivenGate(BaseGate):
    """Ensures we define success criteria upfront and verify them after.

    Two validation modes:
    - validate_plan: checks that the plan has success criteria
    - validate_result: checks that all criteria are verified in the output
    """

    gate_name = "goal_driven"

    def validate_plan(self, plan: ExecutionPlan) -> GateResult:
        """Check that the plan defines verifiable success criteria."""
        issues: list[str] = []

        if not plan.success_criteria:
            issues.append(
                "No success criteria defined. How do we know when this ticket is done?"
            )

        for criterion in plan.success_criteria:
            # Very short criteria are usually not verifiable
            if len(criterion.description.split()) < 3:
                issues.append(
                    f"Criterion '{criterion.id}' is too vague: '{criterion.description}'. "
                    f"Rephrase as something testable."
                )

        # Suggest missing outcome templates for this complexity
        template = OUTCOME_TEMPLATES.get(plan.ticket_complexity, [])
        template_types = {t["type"] for t in template}
        criteria_types = {c.verification_type for c in plan.success_criteria}
        missing = template_types - criteria_types
        if missing:
            issues.append(
                f"For {plan.ticket_complexity.value}, consider adding verification types: "
                f"{missing}"
            )

        return self._make_result(
            issues,
            criteria_count=len(plan.success_criteria),
            ticket_complexity=plan.ticket_complexity.value,
        )

    def validate_result(
        self,
        plan: ExecutionPlan,
        result: ReasonerOutput,
    ) -> GateResult:
        """Check that all success criteria have been verified."""
        issues: list[str] = []

        for criterion in plan.success_criteria:
            verification = result.verifications.get(criterion.id)
            if not verification:
                issues.append(
                    f"Criterion '{criterion.id}' ({criterion.description}) "
                    f"was never verified."
                )
            elif not verification.passed:
                issues.append(
                    f"Criterion '{criterion.id}' ({criterion.description}) "
                    f"FAILED: {verification.reason}"
                )

        return self._make_result(
            issues,
            criteria_total=len(plan.success_criteria),
            criteria_verified=len(result.verifications),
        )
