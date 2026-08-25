"""Policy Engine — the dynamic decision-maker that replaces static plan execution.

Instead of:  Planner → Execute ALL steps → Reasoner
This does:   Policy → ONE Action → Observe → Update Belief → Choose Next → ...

The policy reads the BeliefState after every action and decides:
- What to do next (search? read? hypothesize? generate patch? escalate?)
- WHY (rationale traced for every decision)
- WHEN to stop (confidence threshold, budget, or blockers)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from aviator_core.ticket_solver.budget import TicketBudget
from aviator_core.ticket_solver.gates import GateMode, GateResult
from aviator_core.ticket_solver.gates.simplicity_first import SimplicityGate
from aviator_core.ticket_solver.gates.surgical_changes import SurgicalChangesGate
from aviator_core.ticket_solver.gates.think_before_coding import ThinkBeforeCodingGate
from aviator_core.ticket_solver.models import TicketInput
from aviator_core.ticket_solver.reasoning import (
    ActionType,
    BeliefState,
    PolicyDecision,
    Theory,
    TheoryStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Policy rules — deterministic first, LLM only when needed
# ---------------------------------------------------------------------------

class PolicyEngine:
    """Decides the next action based on current BeliefState.

    Decision priority (checked in order):
    1. Hard stops (budget exhausted, max steps, blockers)
    2. Deterministic rules (enough confidence → patch, no theories → search)
    3. Gate feedback (repeated violations → constrain scope)
    4. LLM-assisted policy (only when deterministic rules aren't clear)
    """

    # Thresholds
    CONFIDENCE_FOR_PATCH = 0.7       # Min belief confidence to attempt patching
    MAX_STEPS = 25                   # Max actions per ticket
    MAX_SEARCH_STEPS = 10            # Max search/read actions before forcing a decision
    MIN_EVIDENCE_FOR_PATCH = 3       # Need at least 3 evidence items before patching
    GATE_VIOLATION_THRESHOLD = 2     # After N violations of same gate, constrain scope

    def __init__(
        self,
        planner_llm: BaseChatModel | None = None,
        gate_mode: GateMode = GateMode.ADVISORY,
    ):
        self.planner_llm = planner_llm  # For LLM-assisted policy when rules aren't enough
        self.gate_violation_counts: dict[str, int] = {}
        self._processed_gate_ids: set[int] = set()  # Prevent double-counting gates

    def decide_next(
        self,
        ticket: TicketInput,
        belief: BeliefState,
        budget: TicketBudget,
        gate_history: list[GateResult] | None = None,
    ) -> PolicyDecision:
        """Decide the next action. Called after EVERY observation.

        Returns a PolicyDecision with action_type, action_name, args, and rationale.
        """
        # ── Check 1: Hard stops ──────────────────────────────────────
        if belief.step_count >= self.MAX_STEPS:
            return PolicyDecision.escalate(
                f"Max steps ({self.MAX_STEPS}) reached without resolution"
            )

        if not budget.can_call_skill() and not belief.is_ready_for_patch():
            return PolicyDecision.escalate("Skill budget exhausted, not ready for patch")

        # ── Check 2: Terminal conditions ─────────────────────────────
        # If we just built successfully with verified criteria → report
        build_evidence = [
            e for e in belief.evidence
            if e.evidence_type.value == "build_passes"
        ]
        if build_evidence and belief.overall_confidence >= 0.9:
            return PolicyDecision.report()

        # ── Check 3: Patches generated but not applied → apply them ──
        # Cycle-aware: count patches generated vs applied, not just 'any ever seen'
        patch_gen_count = sum(
            1 for o in belief.observations if o.action_name == "generate_patch"
        )
        patch_apply_count = sum(
            1
            for o in belief.observations
            if self._is_successful_apply_observation(o)
        )
        recent_apply_failures = sum(
            1
            for o in belief.observations[-3:]
            if o.action_name == "apply_patch" and bool(o.error)
        )
        if patch_gen_count > patch_apply_count and recent_apply_failures >= 2 and budget.can_call_reasoner():
            return PolicyDecision.generate_patch(
                "Patch application failed repeatedly. Regenerating patch against latest context."
            )
        if patch_gen_count > patch_apply_count:
            return PolicyDecision(
                action_type=ActionType.APPLY_PATCH,
                action_name="apply_patch",
                rationale=f"Patch cycle {patch_gen_count}: patches generated but not yet applied.",
                expected_outcome="Files modified on disk, ready for build",
            )

        # ── Check 3b: Patches applied but not built → build ──────────
        build_count = sum(
            1 for o in belief.observations if o.action_name == "build_runner"
        )
        if patch_apply_count > build_count:
            return PolicyDecision(
                action_type=ActionType.BUILD,
                action_name="build_runner",
                rationale=f"Patch cycle {patch_gen_count}: applied but not yet built.",
                expected_outcome="Build pass/fail with structured errors",
            )

        # ── Check 4: Ready to generate patch? ────────────────────────
        if belief.is_ready_for_patch() and budget.can_call_reasoner():
            return PolicyDecision.generate_patch(
                f"Confidence {belief.overall_confidence:.0%} >= threshold. "
                f"Dominant theory: {belief.current_hypothesis}. "
                f"{len(belief.owner_candidates)} target files identified."
            )

        # ── Check 4: Gate feedback → constrain behavior ──────────────
        if gate_history:
            self._update_gate_violations(gate_history)

        scope_constraint = self._get_scope_constraint()

        # ── Check 5: Deterministic action selection ──────────────────
        decision = self._deterministic_policy(ticket, belief, budget, scope_constraint)
        if decision:
            return decision

        # ── Check 6: LLM-assisted policy (last resort) ──────────────
        if self.planner_llm and budget.can_call_planner():
            return self._llm_policy(ticket, belief)

        # ── Fallback: escalate ───────────────────────────────────────
        return PolicyDecision.escalate(
            "No deterministic rule matched and LLM policy unavailable"
        )

    def _deterministic_policy(
        self,
        ticket: TicketInput,
        belief: BeliefState,
        budget: TicketBudget,
        scope_constraint: str,
    ) -> PolicyDecision | None:
        """Pure rule-based action selection — no LLM needed.

        scope_constraint values:
        - 'none': no constraint
        - 'reduce_scope': limit search breadth, fewer files
        - 'simplify': constrain patch to smaller changes
        - 'reduce_scope,simplify': both
        """

        # ── Step 0: No observations yet → initial search ─────────────
        if belief.step_count == 0:
            # Check experience priors first
            if belief.next_best_actions:
                prior_action = belief.next_best_actions[0]
                search_terms = self._extract_search_terms(ticket)
                return PolicyDecision(
                    action_type=ActionType.SEARCH,
                    action_name="grep_codebase",
                    action_args={
                        "pattern": search_terms[0] if search_terms else ticket.title.split()[0],
                        "services": belief.prior_services,
                        "file_types": self._guess_file_types(ticket),
                    },
                    rationale=f"Initial search guided by experience prior: '{prior_action}'. "
                              f"Searching for: {search_terms[0] if search_terms else 'title keyword'}"
                              + (f" within services {belief.prior_services}" if belief.prior_services else ""),
                    expected_outcome="Find files containing relevant patterns (experience-guided)",
                )

            # No priors → extract key terms from ticket
            search_terms = self._extract_search_terms(ticket)
            return PolicyDecision(
                action_type=ActionType.SEARCH,
                action_name="grep_codebase",
                action_args={
                    "pattern": search_terms[0] if search_terms else ticket.title.split()[0],
                    "services": [],
                    "file_types": self._guess_file_types(ticket),
                },
                rationale=f"Initial search — no observations yet. Searching for: {search_terms[0] if search_terms else 'title keyword'}",
                expected_outcome="Find files containing relevant patterns",
            )

        # ── Step 1: Have grep results but no file reads → read them ──
        grep_evidence = [e for e in belief.evidence if e.evidence_type.value == "file_contains_pattern"]
        file_reads_done = "read_file_range" in belief.actions_taken or "read_matched_files" in belief.actions_taken

        if grep_evidence and not file_reads_done:
            # SCOPE CONSTRAINT: reduce_scope → read fewer files
            max_files = 3 if "reduce_scope" in scope_constraint else 5
            top_files = sorted(
                belief.owner_candidates.items(), key=lambda x: -x[1]
            )[:max_files]
            if top_files:
                # SCOPE CONSTRAINT: reduce_scope → less context around matches
                ctx_lines = 25 if "reduce_scope" in scope_constraint else 40
                return PolicyDecision(
                    action_type=ActionType.READ,
                    action_name="read_matched_files",
                    action_args={
                        "matches": [
                            {"file": path, "line": 1}
                            for path, _ in top_files
                        ],
                        "context_lines": ctx_lines,
                    },
                    rationale=f"Have {len(grep_evidence)} grep hits but haven't read file content yet. "
                              f"Reading top {len(top_files)} candidates."
                              + (f" (scope constrained)" if scope_constraint != "none" else ""),
                    expected_outcome="Understand the code around the matches",
                )

        # ── Step 2: Low confidence + have file reads → try different search ──
        # MARGINAL EVIDENCE GAIN: if last 3 searches all found nothing, stop searching
        recent_search_obs = [
            o for o in belief.observations[-3:]
            if o.action_name == "grep_codebase"
        ]
        all_recent_empty = len(recent_search_obs) >= 2 and all(o.is_empty for o in recent_search_obs)

        if (
            belief.overall_confidence < 0.5
            and belief.step_count < self.MAX_SEARCH_STEPS
            and not all_recent_empty  # Marginal gain check
        ):
            # Check what we haven't searched yet
            unsearched = self._find_unsearched_angles(ticket, belief)
            if unsearched:
                return PolicyDecision(
                    action_type=ActionType.SEARCH,
                    action_name="grep_codebase",
                    action_args=unsearched,
                    rationale=f"Confidence only {belief.overall_confidence:.0%}. "
                              f"Trying alternative search angle.",
                    expected_outcome="Find additional evidence to raise confidence",
                )

        # ── Step 3: Have evidence but no theories → hypothesize ──────
        if len(belief.evidence) >= 3 and not belief.theories and budget.can_call_planner():
            return PolicyDecision(
                action_type=ActionType.HYPOTHESIZE,
                action_name="hypothesize",
                rationale=f"Have {len(belief.evidence)} evidence items but no theories. "
                          f"Need LLM to form hypotheses.",
                expected_outcome="Generate theories about root cause",
            )

        # ── Step 4: Contradictions found → investigate ───────────────
        if belief.contradictions and belief.step_count < self.MAX_SEARCH_STEPS:
            return PolicyDecision(
                action_type=ActionType.SEARCH,
                action_name="grep_codebase",
                action_args={
                    "pattern": belief.contradictions[0][:50],
                    "services": [],
                },
                rationale=f"Contradiction found: {belief.contradictions[0][:80]}. "
                          f"Searching to resolve.",
                expected_outcome="Resolve the contradiction",
            )

        # ── Step 5: Moderate confidence but missing info → targeted read ──
        if 0.4 <= belief.overall_confidence < 0.7 and belief.missing_info:
            return PolicyDecision(
                action_type=ActionType.SEARCH,
                action_name="grep_codebase",
                action_args={
                    "pattern": belief.missing_info[0],
                    "services": [],
                },
                rationale=f"Confidence at {belief.overall_confidence:.0%} but missing: {belief.missing_info[0]}",
                expected_outcome=f"Find: {belief.missing_info[0]}",
            )

        # ── Step 6: Enough evidence, force patch attempt ─────────────
        # SCOPE CONSTRAINT: simplify → require more evidence before patching
        min_evidence = self.MIN_EVIDENCE_FOR_PATCH + (2 if "simplify" in scope_constraint else 0)
        if (
            len(belief.evidence) >= min_evidence
            and belief.owner_candidates
            and budget.can_call_reasoner()
        ):
            constraint_note = " (simplify constraint: requiring more evidence)" if "simplify" in scope_constraint else ""
            return PolicyDecision.generate_patch(
                f"Have {len(belief.evidence)} evidence items and "
                f"{len(belief.owner_candidates)} candidate files. "
                f"Forcing patch attempt at {belief.overall_confidence:.0%} confidence."
                + constraint_note
            )

        return None

    def _llm_policy(
        self,
        ticket: TicketInput,
        belief: BeliefState,
    ) -> PolicyDecision:
        """Ask the LLM to decide next action when rules aren't enough."""
        system = (
            "You are a policy engine for a ticket-solving agent. "
            "Given the current belief state, decide the single best next action. "
            "Output JSON: {\"action_type\": \"search|read|hypothesize|generate_patch|ask_user|escalate\", "
            "\"action_name\": \"skill_block_name\", \"action_args\": {}, "
            "\"rationale\": \"why this action\"}"
        )
        user = (
            f"## Ticket: {ticket.title}\n{ticket.description[:500]}\n\n"
            f"{belief.to_context_string()}\n\n"
            f"Steps taken so far: {belief.step_count}\n"
            f"Actions taken: {belief.actions_taken[-5:]}\n"  # Last 5 only
            f"What should we do next?"
        )

        try:
            response = self.planner_llm.invoke([
                SystemMessage(content=system),
                HumanMessage(content=user),
            ])
            raw = response.content if hasattr(response, "content") else str(response)

            # Parse JSON
            json_str = raw
            if "```json" in raw:
                start = raw.index("```json") + 7
                end = raw.index("```", start)
                json_str = raw[start:end].strip()

            data = json.loads(json_str)
            decision = PolicyDecision(
                action_type=ActionType(data.get("action_type", "search")),
                action_name=data.get("action_name", "grep_codebase"),
                action_args=data.get("action_args", {}),
                rationale=f"[LLM policy] {data.get('rationale', 'LLM decided')}",
            )
            # Record LLM token usage (estimate: ~4 chars per token)
            decision.action_args["_policy_tokens"] = len(raw) // 4
            return decision
        except Exception as e:
            logger.warning("LLM policy failed: %s — falling back to escalate", e)
            return PolicyDecision.escalate(f"LLM policy failed: {e}")

    # ── Helper methods ───────────────────────────────────────────────

    def _extract_search_terms(self, ticket: TicketInput) -> list[str]:
        """Extract meaningful search terms from ticket text."""
        text = f"{ticket.title} {ticket.description}"
        # Remove common stop words
        stop_words = {
            "the", "is", "in", "to", "and", "of", "a", "for", "on", "it",
            "be", "as", "that", "this", "are", "was", "with", "not", "but",
            "should", "needs", "need", "when", "from", "or", "an", "has",
            "have", "will", "can", "been", "would", "could", "update",
        }
        words = [w.strip(".,;:!?()[]{}\"'") for w in text.split()]
        terms = [w for w in words if w.lower() not in stop_words and len(w) > 2]

        # Prioritize: camelCase words, technical terms, specific names
        technical = [w for w in terms if any(c.isupper() for c in w[1:])]  # camelCase
        specific = [w for w in terms if w[0].isupper() and not w.isupper()]  # ProperCase
        remaining = [w for w in terms if w not in technical and w not in specific]

        return (technical + specific + remaining)[:5]

    def _guess_file_types(self, ticket: TicketInput) -> list[str]:
        """Guess file types from ticket labels and description."""
        text = f"{' '.join(ticket.labels)} {ticket.description}".lower()
        if "angular" in text or "typescript" in text or "component" in text or "xchange-ui" in text:
            return ["*.ts", "*.html"]
        if "css" in text or "style" in text or "alignment" in text:
            return ["*.ts", "*.css", "*.scss"]
        if "pom" in text or "version" in text or "maven" in text:
            return ["*.xml"]
        if "config" in text or "property" in text or "application" in text:
            return ["*.yml", "*.yaml", "*.properties"]
        return ["*.java"]  # Default for CC4E backend

    def _find_unsearched_angles(
        self,
        ticket: TicketInput,
        belief: BeliefState,
    ) -> dict[str, Any] | None:
        """Find search angles we haven't tried yet."""
        terms = self._extract_search_terms(ticket)
        searched = set()

        # Collect patterns we've already searched for
        for obs in belief.observations:
            if obs.action_name == "grep_codebase":
                raw = obs.raw_data if isinstance(obs.raw_data, dict) else {}
                searched.add(raw.get("pattern", ""))

        # Find first unsearched term
        for term in terms:
            if term not in searched:
                return {
                    "pattern": term,
                    "services": [],
                    "file_types": self._guess_file_types(ticket),
                }

        # Try regex patterns if literal search didn't work
        if ".equals" not in searched and any(
            kw in ticket.description.lower()
            for kw in ["case", "sensitive", "compare", "match"]
        ):
            return {
                "pattern": r"\.equals\(",
                "services": [],
                "file_types": ["*.java"],
                "is_regex": True,
            }

        return None

    def _update_gate_violations(self, gate_history: list[GateResult]) -> None:
        """Track gate violation frequency for policy adaptation.

        Uses object identity (id()) to prevent double-counting gates
        that persist across multiple decide_next() calls.
        """
        for result in gate_history:
            gate_id = id(result)
            if gate_id in self._processed_gate_ids:
                continue  # Already counted this gate result
            self._processed_gate_ids.add(gate_id)

            if not result.passed:
                count = self.gate_violation_counts.get(result.gate_name, 0)
                self.gate_violation_counts[result.gate_name] = count + 1

    def _get_scope_constraint(self) -> str:
        """If a gate is repeatedly violated, emit a constraint signal."""
        constraints: list[str] = []

        surgical_violations = self.gate_violation_counts.get("surgical_changes", 0)
        if surgical_violations >= self.GATE_VIOLATION_THRESHOLD:
            constraints.append("reduce_scope")  # Smaller patches

        simplicity_violations = self.gate_violation_counts.get("simplicity_first", 0)
        if simplicity_violations >= self.GATE_VIOLATION_THRESHOLD:
            constraints.append("simplify")  # Fewer lines

        return ",".join(constraints) if constraints else "none"

    @staticmethod
    def _is_successful_apply_observation(observation: Any) -> bool:
        """Treat apply_patch as successful only when no failures were reported."""
        if getattr(observation, "action_name", "") != "apply_patch":
            return False
        if getattr(observation, "error", ""):
            return False
        raw = getattr(observation, "raw_data", {})
        if not isinstance(raw, dict):
            return False
        return int(raw.get("total_failed", 0) or 0) == 0
