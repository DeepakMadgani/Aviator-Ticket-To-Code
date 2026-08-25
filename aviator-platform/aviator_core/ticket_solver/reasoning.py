"""Adaptive reasoning models — Observation, Evidence, Theory, BeliefState, PolicyDecision.

This is the core intelligence layer that replaces the static Planner → Executor → Reasoner
pipeline with a dynamic Policy → Action → Observe → Update → Choose loop.

Architecture:

    ┌──────────────────────────────────────────────┐
    │              ADAPTIVE LOOP                   │
    │                                              │
    │   Policy ──→ Action ──→ Observation          │
    │      ↑                      │                │
    │      │                      ▼                │
    │      │               Evidence Layer          │
    │      │              (normalize, score)        │
    │      │                      │                │
    │      │                      ▼                │
    │      │                  Theory               │
    │      │              (hypotheses)             │
    │      │                      │                │
    │      │                      ▼                │
    │      └──── BeliefState ◄────┘                │
    │           (updated after                     │
    │            every action)                     │
    └──────────────────────────────────────────────┘
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Observation — raw output from any action
# ---------------------------------------------------------------------------

class ObservationType(str, Enum):
    """What kind of action produced this observation."""
    GREP_RESULT = "grep_result"
    FILE_READ = "file_read"
    SYMBOL_SEARCH = "symbol_search"
    CALLER_SEARCH = "caller_search"
    BUILD_RESULT = "build_result"
    CONFIG_READ = "config_read"
    GIT_DIFF = "git_diff"
    LLM_RESPONSE = "llm_response"
    USER_RESPONSE = "user_response"


class Observation(BaseModel):
    """Raw, uninterpreted output from a single action.

    Every action produces exactly one Observation. Observations are
    never modified — they are immutable facts.
    """
    id: str                           # Unique ID (e.g., "obs_001")
    action_name: str                  # Which skill/action produced this
    observation_type: ObservationType
    raw_data: Any                     # The actual output (matches, file content, etc.)
    timestamp: float = Field(default_factory=time.time)
    is_empty: bool = False            # True if action returned nothing useful
    error: str = ""                   # Non-empty if the action failed

    def summary(self) -> str:
        """One-line human summary for logging."""
        if self.error:
            return f"[{self.action_name}] ERROR: {self.error}"
        if self.is_empty:
            return f"[{self.action_name}] No results"
        # Summarize based on type
        if isinstance(self.raw_data, list):
            return f"[{self.action_name}] {len(self.raw_data)} results"
        if isinstance(self.raw_data, dict):
            keys = list(self.raw_data.keys())[:3]
            return f"[{self.action_name}] keys={keys}"
        return f"[{self.action_name}] data_len={len(str(self.raw_data))}"


# ---------------------------------------------------------------------------
# Evidence — normalized, scored claims extracted from Observations
# ---------------------------------------------------------------------------

class EvidenceType(str, Enum):
    """What the evidence claims."""
    FILE_CONTAINS_PATTERN = "file_contains_pattern"
    FILE_MISSING_PATTERN = "file_missing_pattern"
    METHOD_EXISTS = "method_exists"
    METHOD_CALLED_BY = "method_called_by"
    CONFIG_VALUE_IS = "config_value_is"
    BUILD_PASSES = "build_passes"
    BUILD_FAILS = "build_fails"
    OWNER_CANDIDATE = "owner_candidate"
    VERSION_VALUE = "version_value"
    DEPENDENCY_CHAIN = "dependency_chain"


class Evidence(BaseModel):
    """A normalized, scored claim derived from one or more Observations.

    Unlike raw Observations, Evidence has:
    - A typed claim (what does this mean?)
    - A confidence score (how reliable?)
    - A source chain (which observations back this up?)
    """
    id: str                           # e.g., "ev_001"
    evidence_type: EvidenceType
    claim: str                        # Human-readable: "email comparison is case-sensitive in MemberService.java"
    confidence: float                 # 0.0 to 1.0
    source_observations: list[str] = Field(default_factory=list)  # IDs of backing observations
    file_path: str = ""               # Which file this evidence is about (if applicable)
    line_number: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def supports(self, theory_id: str) -> bool:
        """Placeholder for theory-evidence linking."""
        return theory_id in self.metadata.get("supports_theories", [])

    def contradicts(self, theory_id: str) -> bool:
        return theory_id in self.metadata.get("contradicts_theories", [])


# ---------------------------------------------------------------------------
# Theory — hypotheses about the ticket's root cause and fix
# ---------------------------------------------------------------------------

class TheoryStatus(str, Enum):
    """Lifecycle of a theory."""
    PROPOSED = "proposed"         # Just created from initial ticket analysis
    SUPPORTED = "supported"       # Has evidence backing it
    CONTRADICTED = "contradicted" # Evidence against it
    CONFIRMED = "confirmed"       # Strong evidence + build pass
    ABANDONED = "abandoned"       # Replaced by a better theory


class Theory(BaseModel):
    """A hypothesis about the root cause and/or fix for the ticket.

    Theories evolve as evidence accumulates. The BeliefState tracks
    which theory is currently dominant.
    """
    id: str                          # e.g., "th_001"
    hypothesis: str                  # "The bug is caused by .equals() instead of .equalsIgnoreCase()"
    proposed_fix: str = ""           # "Replace .equals(email) with .equalsIgnoreCase(email)"
    status: TheoryStatus = TheoryStatus.PROPOSED
    confidence: float = 0.5          # Evolves with evidence
    supporting_evidence: list[str] = Field(default_factory=list)  # Evidence IDs
    contradicting_evidence: list[str] = Field(default_factory=list)
    target_files: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)

    def update_confidence(self, evidence: Evidence) -> None:
        """Adjust confidence based on new evidence."""
        if evidence.id in self.supporting_evidence:
            return  # Already counted
        if evidence.supports(self.id):
            self.supporting_evidence.append(evidence.id)
            self.confidence = min(1.0, self.confidence + evidence.confidence * 0.2)
            if self.confidence >= 0.8:
                self.status = TheoryStatus.SUPPORTED
        elif evidence.contradicts(self.id):
            self.contradicting_evidence.append(evidence.id)
            self.confidence = max(0.0, self.confidence - evidence.confidence * 0.3)
            if self.confidence <= 0.2:
                self.status = TheoryStatus.CONTRADICTED


# ---------------------------------------------------------------------------
# BeliefState — the agent's evolving understanding (updated after EVERY action)
# ---------------------------------------------------------------------------

class BeliefState(BaseModel):
    """The agent's current understanding of the ticket — updated after every action.

    This is the central state object. The policy reads it to decide the next action.
    """
    # Current understanding
    current_hypothesis: str = ""                    # Best guess right now
    dominant_theory_id: str = ""                    # ID of the strongest theory

    # Knowledge gathered
    theories: list[Theory] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)

    # Confidence tracking
    overall_confidence: float = 0.0                 # 0.0 = no idea, 1.0 = certain
    missing_info: list[str] = Field(default_factory=list)  # What we still need to find

    # File ownership
    owner_candidates: dict[str, float] = Field(default_factory=dict)  # file → confidence

    # Contradictions and blockers
    contradictions: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)

    # Action history
    actions_taken: list[str] = Field(default_factory=list)
    actions_failed: list[str] = Field(default_factory=list)
    next_best_actions: list[str] = Field(default_factory=list)
    prior_services: list[str] = Field(default_factory=list)

    # Meta
    step_count: int = 0
    last_updated: float = Field(default_factory=time.time)

    def add_observation(self, obs: Observation) -> None:
        """Record a new observation and update step count."""
        self.observations.append(obs)
        self.actions_taken.append(obs.action_name)
        if obs.error:
            self.actions_failed.append(f"{obs.action_name}: {obs.error}")
        self.step_count += 1
        self.last_updated = time.time()

    def add_evidence(self, ev: Evidence) -> None:
        """Add evidence and update theories."""
        self.evidence.append(ev)
        # Update all theories with this new evidence
        for theory in self.theories:
            theory.update_confidence(ev)
        # Update dominant theory
        self._update_dominant_theory()
        self.last_updated = time.time()

    def add_theory(self, theory: Theory) -> None:
        """Add a new theory to the belief state."""
        self.theories.append(theory)
        self._update_dominant_theory()

    def _update_dominant_theory(self) -> None:
        """Set dominant theory to the one with highest confidence."""
        if not self.theories:
            return
        active = [t for t in self.theories if t.status not in (TheoryStatus.ABANDONED, TheoryStatus.CONTRADICTED)]
        if active:
            best = max(active, key=lambda t: t.confidence)
            self.dominant_theory_id = best.id
            self.current_hypothesis = best.hypothesis
            self.overall_confidence = best.confidence

    def is_ready_for_patch(self) -> bool:
        """Are we confident enough to generate patches?"""
        return (
            self.overall_confidence >= 0.7
            and len(self.owner_candidates) > 0
            and len(self.contradictions) == 0
        )

    def to_context_string(self) -> str:
        """Compact summary for LLM context — keeps token usage low."""
        parts = [
            f"## Current Understanding (confidence: {self.overall_confidence:.0%})",
            f"Hypothesis: {self.current_hypothesis}",
        ]
        if self.theories:
            parts.append(f"\n### Theories ({len(self.theories)})")
            for t in self.theories:
                parts.append(f"- [{t.status.value}] {t.hypothesis} (conf: {t.confidence:.0%})")
        if self.owner_candidates:
            parts.append(f"\n### Likely Files to Modify")
            for path, conf in sorted(self.owner_candidates.items(), key=lambda x: -x[1])[:5]:
                parts.append(f"- {path} ({conf:.0%})")
        if self.missing_info:
            parts.append(f"\n### Still Unknown")
            for m in self.missing_info:
                parts.append(f"- {m}")
        if self.contradictions:
            parts.append(f"\n### Contradictions")
            for c in self.contradictions:
                parts.append(f"- ⚠️ {c}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# PolicyDecision — what the agent decides to do next
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    """Types of actions the policy can choose."""
    SEARCH = "search"                # grep, symbol search, etc.
    READ = "read"                    # Read specific file/lines
    QUERY = "query"                  # Neo4j, SQLite query
    HYPOTHESIZE = "hypothesize"      # Ask LLM to form theories
    GENERATE_PATCH = "generate_patch"  # Ask Reasoner to write code
    APPLY_PATCH = "apply_patch"      # Apply patches to workspace files
    BUILD = "build"                  # Run build
    ASK_USER = "ask_user"            # Need clarification
    ESCALATE = "escalate"            # Give up, hand to human
    REPORT = "report"                # Generate final summary


class PolicyDecision(BaseModel):
    """The policy's choice of what to do next.

    Includes the rationale so we can trace WHY each action was taken.
    """
    action_type: ActionType
    action_name: str              # Specific skill block name
    action_args: dict[str, Any] = Field(default_factory=dict)
    rationale: str                # WHY this action right now
    expected_outcome: str = ""    # What we hope to learn
    priority: float = 1.0         # Higher = more important
    is_terminal: bool = False     # True for ESCALATE, REPORT

    @classmethod
    def escalate(cls, reason: str) -> "PolicyDecision":
        return cls(
            action_type=ActionType.ESCALATE,
            action_name="escalate",
            rationale=reason,
            is_terminal=True,
        )

    @classmethod
    def generate_patch(cls, rationale: str) -> "PolicyDecision":
        return cls(
            action_type=ActionType.GENERATE_PATCH,
            action_name="generate_patch",
            rationale=rationale,
        )

    @classmethod
    def report(cls) -> "PolicyDecision":
        return cls(
            action_type=ActionType.REPORT,
            action_name="report",
            rationale="All criteria verified. Generating summary.",
            is_terminal=True,
        )


# ---------------------------------------------------------------------------
# Experience — persisted after each ticket for cross-ticket learning
# ---------------------------------------------------------------------------

class TicketExperience(BaseModel):
    """Post-mortem record persisted after every ticket — drives future policy.

    Loaded as priors for similar tickets to improve over time.
    """
    ticket_id: str
    ticket_type: str                  # complexity class
    winning_action_sequence: list[str] = Field(default_factory=list)
    failed_action_sequences: list[list[str]] = Field(default_factory=list)
    gate_violations: list[dict[str, Any]] = Field(default_factory=list)
    effective_recovery_paths: list[str] = Field(default_factory=list)
    total_steps: int = 0
    total_tokens: int = 0
    total_time_seconds: float = 0.0
    final_confidence: float = 0.0
    outcome: str = ""                 # "success", "escalated", "error"
    key_learnings: list[str] = Field(default_factory=list)
    timestamp: float = Field(default_factory=time.time)
