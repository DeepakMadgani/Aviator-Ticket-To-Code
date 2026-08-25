"""Recovery Decision Contract — prevents retry oscillation."""

from __future__ import annotations

import logging

from aviator_core.ticket_solver.models import BuildError, RecoveryDecision

logger = logging.getLogger(__name__)


class RecoveryEngine:
    """Manages retry decisions with failure fingerprinting.

    Key rules:
    - Same fingerprint twice = stop (oscillating)
    - Must propose a new strategy each time
    - Max retries enforced by budget, not here
    """

    def should_retry(self, history: list[RecoveryDecision]) -> bool:
        """Decide whether another retry makes sense."""
        if not history:
            return True

        # Same failure fingerprint repeated = oscillating → stop
        fingerprints = [h.failure_fingerprint for h in history]
        if len(fingerprints) != len(set(fingerprints)):
            logger.warning(
                "Recovery: same failure fingerprint repeated — stopping retries. "
                "Fingerprints: %s", fingerprints,
            )
            return False

        # No new strategy proposed = nothing to try → stop
        last = history[-1]
        if not last.next_strategy:
            logger.warning("Recovery: no new strategy proposed — stopping retries.")
            return False

        return True

    def create_decision(
        self,
        error: BuildError,
        history: list[RecoveryDecision],
    ) -> RecoveryDecision:
        """Create a recovery decision for the given build error.

        Fingerprint uses normalized error_class + symbol + message_pattern
        (not file:line) per Codex caution #3.
        """
        fingerprint = error.fingerprint

        # Classify error and pick strategy
        strategy_map = {
            "compilation": ("fix_syntax", "Compilation error — likely a typo or missing import"),
            "test_failure": ("fix_assertion", "Test fails — logic fix needed, not just syntax"),
            "dependency": ("add_missing_dependency", "Missing class/method — search for correct import"),
        }
        strategy, rationale = strategy_map.get(
            error.error_type,
            ("general_fix", "Unknown error class — provide full error context"),
        )

        # If this strategy was already tried, escalate
        tried = [h.next_strategy for h in history]
        if strategy in tried:
            strategy = f"escalate_from_{strategy}"
            rationale = f"Previous strategy '{strategy}' was already tried. Trying broader fix."

        decision = RecoveryDecision(
            failure_fingerprint=fingerprint,
            attempt_number=len(history) + 1,
            strategy_history=tried,
            next_strategy=strategy,
            next_strategy_rationale=rationale,
            stop_condition="Same fingerprint repeated, or budget exhausted",
        )

        logger.info(
            "Recovery decision #%d: fingerprint=%s strategy=%s",
            decision.attempt_number, fingerprint, strategy,
        )
        return decision
