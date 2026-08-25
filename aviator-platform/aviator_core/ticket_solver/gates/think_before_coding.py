"""Karpathy Principle 1: Think Before Coding — Ambiguity scoring gate."""

from __future__ import annotations

from typing import Any

from aviator_core.ticket_solver.gates import BaseGate, GateResult
from aviator_core.ticket_solver.models import ExecutionPlan, TicketInput

# Known CC4E microservice names for multi-service ambiguity detection
CC4E_SERVICES = [
    "area-service", "project-service", "sagas-service",
    "deliverable-service", "transmittal-service", "email-service",
    "gateway-service", "notification-service", "xchange-ui",
]


class ThinkBeforeCodingGate(BaseGate):
    """Rejects plans that hide assumptions or silently pick interpretations.

    Uses multi-signal ambiguity scoring instead of a single crude threshold.
    """

    gate_name = "think_before_coding"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.ambiguity_threshold = 0.45  # Tunable after calibration

    def score_ambiguity(self, ticket: TicketInput, plan: ExecutionPlan) -> tuple[float, list[str]]:
        """Returns (score, signal_descriptions).

        Score ranges from 0.0 (crystal clear) to 1.0 (completely ambiguous).
        """
        signals: list[tuple[str, float]] = []

        # Signal 1: Very short description (weak — weight 0.15)
        word_count = len(ticket.description.split())
        if word_count < 30:
            signals.append(("short_description (<30 words)", 0.15))

        # Signal 2: No reproduction steps (weight 0.20)
        repro_keywords = [
            "steps to reproduce", "how to reproduce", "login",
            "click", "navigate", "open the", "go to",
        ]
        has_repro = any(kw in ticket.description.lower() for kw in repro_keywords)
        if not has_repro:
            signals.append(("no_reproduction_steps", 0.20))

        # Signal 3: No actual/expected behavior described (weight 0.15)
        behavior_keywords = ["actual behavior", "expected behavior", "actual:", "expected:"]
        has_behavior = any(kw in ticket.description.lower() for kw in behavior_keywords)
        if not has_behavior and word_count < 100:
            signals.append(("no_actual_expected_behavior", 0.15))

        # Signal 4: Planner stated zero assumptions (weight 0.30)
        if not plan.assumptions:
            signals.append(("no_assumptions_stated", 0.30))

        # Signal 5: Ticket mentions multiple services without clarity (weight 0.10)
        services_mentioned = [s for s in CC4E_SERVICES if s in ticket.description.lower()]
        if len(services_mentioned) > 2:
            signals.append(("multi_service_ambiguity", 0.10))

        # Signal 6: No acceptance criteria (weight 0.10)
        if not ticket.acceptance_criteria:
            signals.append(("no_acceptance_criteria", 0.10))

        score = sum(weight for _, weight in signals)
        descriptions = [name for name, _ in signals]
        return score, descriptions

    def validate(self, ticket: TicketInput, plan: ExecutionPlan) -> GateResult:
        """Validate that the plan doesn't hide assumptions or ambiguity."""
        score, signal_descriptions = self.score_ambiguity(ticket, plan)
        issues: list[str] = []

        if score >= self.ambiguity_threshold:
            issues.append(
                f"Ambiguity score {score:.2f} >= threshold {self.ambiguity_threshold}. "
                f"Signals: {signal_descriptions}"
            )

        if not plan.assumptions:
            issues.append("No assumptions stated — what are you assuming about the codebase?")

        # Multiple interpretations should have an explained choice
        if len(plan.alternative_approaches) > 1 and not plan.recommended_approach_reason:
            issues.append(
                "Multiple approaches listed but no reason given for the recommended one."
            )

        return self._make_result(
            issues,
            ambiguity_score=score,
            signal_descriptions=signal_descriptions,
        )
