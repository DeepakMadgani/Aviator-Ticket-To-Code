"""Experience Store — persists and retrieves cross-ticket learning.

After every ticket, the winning action sequence, failed paths, gate violations,
and recovery strategies are saved. Future tickets load relevant priors.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from aviator_core.ticket_solver.reasoning import BeliefState, TicketExperience

logger = logging.getLogger(__name__)

DEFAULT_STORE_PATH = Path(__file__).parent / "experience_store.jsonl"

KNOWN_SERVICES = [
    "area-service", "project-service", "sagas-service",
    "deliverable-service", "transmittal-service", "email-service",
    "gateway-service", "notification-service", "xchange-ui",
]


class ExperienceStore:
    """Simple file-backed experience store (JSONL format).

    Each line is one TicketExperience record. For Phase A this is
    sufficient — upgrade to SQLite/Postgres later if needed.
    """

    def __init__(self, store_path: Path | str = DEFAULT_STORE_PATH):
        self.store_path = Path(store_path)

    def save(self, experience: TicketExperience) -> None:
        """Append a ticket experience to the store."""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "a", encoding="utf-8") as f:
            f.write(experience.model_dump_json() + "\n")
        logger.info(
            "Experience saved: ticket=%s outcome=%s steps=%d",
            experience.ticket_id, experience.outcome, experience.total_steps,
        )

    def load_all(self) -> list[TicketExperience]:
        """Load all past experiences."""
        if not self.store_path.exists():
            return []
        experiences: list[TicketExperience] = []
        with open(self.store_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        experiences.append(TicketExperience.model_validate_json(line))
                    except Exception as e:
                        logger.warning("Failed to parse experience line: %s", e)
        return experiences

    def find_similar(self, ticket_type: str, limit: int = 5) -> list[TicketExperience]:
        """Find past experiences for similar ticket types.

        Used as policy priors — "for config_change tickets, this action
        sequence worked 80% of the time".
        """
        all_exp = self.load_all()
        matches = [e for e in all_exp if e.ticket_type == ticket_type]
        # Sort by most recent first
        matches.sort(key=lambda e: e.timestamp, reverse=True)
        return matches[:limit]

    def get_winning_patterns(self, ticket_type: str) -> dict[str, Any]:
        """Extract common patterns from successful tickets of this type.

        Returns a summary the policy engine can use as priors.
        """
        similar = self.find_similar(ticket_type, limit=20)
        successes = [e for e in similar if e.outcome == "success"]

        if not successes:
            return {"has_priors": False}

        # Count action sequences
        action_counts: dict[str, int] = {}
        for exp in successes:
            for action in exp.winning_action_sequence:
                action_counts[action] = action_counts.get(action, 0) + 1

        # Average metrics
        avg_steps = sum(e.total_steps for e in successes) / len(successes)
        avg_tokens = sum(e.total_tokens for e in successes) / len(successes)
        avg_confidence = sum(e.final_confidence for e in successes) / len(successes)

        return {
            "has_priors": True,
            "sample_size": len(successes),
            "common_actions": sorted(action_counts.items(), key=lambda x: -x[1])[:10],
            "common_services": self._common_services(successes),
            "avg_steps": avg_steps,
            "avg_tokens": avg_tokens,
            "avg_confidence": avg_confidence,
            "common_gate_violations": self._common_violations(successes),
        }

    @staticmethod
    def _common_services(experiences: list[TicketExperience]) -> list[str]:
        """Infer frequent services from recorded key learning file paths."""
        counts: Counter[str] = Counter()
        for exp in experiences:
            for learning in exp.key_learnings:
                if "Files modified:" not in learning:
                    continue
                text = learning.lower()
                for svc in KNOWN_SERVICES:
                    if svc in text:
                        counts[svc] += 1
        return [svc for svc, _ in counts.most_common(3)]

    @staticmethod
    def _common_violations(experiences: list[TicketExperience]) -> list[str]:
        """Find gate violations that occurred even in successful tickets."""
        violation_counts: dict[str, int] = {}
        for exp in experiences:
            for v in exp.gate_violations:
                gate = v.get("gate", "unknown")
                violation_counts[gate] = violation_counts.get(gate, 0) + 1
        return [g for g, c in sorted(violation_counts.items(), key=lambda x: -x[1]) if c > 1]

    def create_experience_from_belief(
        self,
        ticket_id: str,
        ticket_type: str,
        belief: BeliefState,
        outcome: str,
        total_tokens: int,
        total_time: float,
        gate_logs: list[dict[str, Any]],
    ) -> TicketExperience:
        """Build an experience record from the final belief state."""
        return TicketExperience(
            ticket_id=ticket_id,
            ticket_type=ticket_type,
            winning_action_sequence=belief.actions_taken if outcome == "success" else [],
            failed_action_sequences=[belief.actions_failed] if belief.actions_failed else [],
            gate_violations=[g for g in gate_logs if not g.get("passed", True)],
            effective_recovery_paths=[],
            total_steps=belief.step_count,
            total_tokens=total_tokens,
            total_time_seconds=total_time,
            final_confidence=belief.overall_confidence,
            outcome=outcome,
            key_learnings=[
                f"Dominant theory: {belief.current_hypothesis}",
                f"Files modified: {list(belief.owner_candidates.keys())[:5]}",
            ],
        )
