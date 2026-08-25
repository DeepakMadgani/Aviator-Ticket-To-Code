"""
RunContext — per-run request-scoped telemetry, budget, and health.

Implements B11 (RunContext + GlobalRunBudget) and B12 (DegradationRegistry + RunHealth).

Design principles:
  - One RunContext per ticket run, never shared across concurrent runs.
  - Budget is checked at every LLM call boundary (charge before proceed).
  - DegradationRegistry is monotonic: HEALTHY → DEGRADED → FAILED only.
  - run_health is derived (never stored) and reflects the worst subsystem status.
  - RunRecord is emitted once at the terminal node for telemetry.

Author: Deepak Madgani
Date: July 2026
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMS
# ============================================================================

class HealthLevel(str, Enum):
    """Monotonic health levels — transitions only go downward."""
    HEALTHY  = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED   = "FAILED"

    def __lt__(self, other: "HealthLevel") -> bool:
        _order = {HealthLevel.HEALTHY: 0, HealthLevel.DEGRADED: 1, HealthLevel.FAILED: 2}
        return _order[self] < _order[other]

    def __le__(self, other: "HealthLevel") -> bool:
        return self == other or self < other

    def is_worse_than(self, other: "HealthLevel") -> bool:
        return self > other  # type: ignore[operator]


# ============================================================================
# EXCEPTIONS
# ============================================================================

class BudgetExceeded(RuntimeError):
    """Raised when any RunBudget ceiling is breached."""
    def __init__(self, kind: str, limit, actual) -> None:
        self.kind = kind
        self.limit = limit
        self.actual = actual
        super().__init__(
            f"RunBudget exceeded: {kind} limit={limit} actual={actual}"
        )


# ============================================================================
# BUDGET
# ============================================================================

@dataclass
class RunBudget:
    """
    Hard ceilings on per-run resource consumption (B11).

    All ceilings are configurable so tests can set them small.
    Budget is charged at the LLM wrapper boundary; nodes only *check*.
    """
    max_wall_s:   float = 900.0   # 15 min default
    max_llm_calls: int  = 50
    max_tokens:   int   = 500_000
    max_cost_usd: float = 5.0

    # Live counters (mutated by charge())
    elapsed_s:   float = field(default=0.0, init=False)
    llm_calls:   int   = field(default=0,   init=False)
    tokens_in:   int   = field(default=0,   init=False)
    tokens_out:  int   = field(default=0,   init=False)
    cost_usd:    float = field(default=0.0, init=False)

    _start_wall: float = field(default_factory=time.monotonic, init=False, repr=False)

    def _refresh_elapsed(self) -> None:
        self.elapsed_s = time.monotonic() - self._start_wall

    def charge(
        self,
        tokens_in:  int   = 0,
        tokens_out: int   = 0,
        cost_usd:   float = 0.0,
    ) -> None:
        """
        Record one LLM call's resource consumption and check all ceilings.

        Raises BudgetExceeded if any limit is breached.
        Must be called inside a finally block so partial charges are recorded
        even when the call raises.
        """
        self._refresh_elapsed()
        self.llm_calls  += 1
        self.tokens_in  += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd   += cost_usd

        logger.debug(
            "RunBudget.charge: calls=%d tokens=%d cost=%.4f elapsed=%.1fs",
            self.llm_calls, self.tokens_in + self.tokens_out, self.cost_usd, self.elapsed_s,
        )

        if self.elapsed_s > self.max_wall_s:
            raise BudgetExceeded("wall_s", self.max_wall_s, round(self.elapsed_s, 1))
        if self.llm_calls > self.max_llm_calls:
            raise BudgetExceeded("llm_calls", self.max_llm_calls, self.llm_calls)
        if (self.tokens_in + self.tokens_out) > self.max_tokens:
            raise BudgetExceeded("tokens", self.max_tokens, self.tokens_in + self.tokens_out)
        if self.cost_usd > self.max_cost_usd:
            raise BudgetExceeded("cost_usd", self.max_cost_usd, round(self.cost_usd, 4))

    def check(self) -> None:
        """Check wall-clock limit without charging. Used at node boundaries."""
        self._refresh_elapsed()
        if self.elapsed_s > self.max_wall_s:
            raise BudgetExceeded("wall_s", self.max_wall_s, round(self.elapsed_s, 1))

    def remaining(self) -> Dict[str, float]:
        self._refresh_elapsed()
        return {
            "wall_s":    max(0.0, self.max_wall_s   - self.elapsed_s),
            "llm_calls": max(0,   self.max_llm_calls - self.llm_calls),
            "tokens":    max(0,   self.max_tokens    - (self.tokens_in + self.tokens_out)),
            "cost_usd":  max(0.0, self.max_cost_usd  - self.cost_usd),
        }

    def budget_exceeded(self) -> bool:
        self._refresh_elapsed()
        return (
            self.elapsed_s > self.max_wall_s
            or self.llm_calls > self.max_llm_calls
            or (self.tokens_in + self.tokens_out) > self.max_tokens
            or self.cost_usd > self.max_cost_usd
        )


# ============================================================================
# DEGRADATION REGISTRY (B12)
# ============================================================================

@dataclass
class DegradationRegistry:
    """
    Tracks the health of every subsystem for this run.

    Transitions are monotonic: HEALTHY → DEGRADED → FAILED.
    All subsystems start HEALTHY; callers call .degrade() on any failure.
    """
    _registry: Dict[str, HealthLevel] = field(default_factory=dict, init=False)
    _reasons:  Dict[str, List[str]]   = field(default_factory=dict, init=False)

    # Subsystems whose FAILED status must block the run from claiming success.
    EVIDENCE_CRITICAL = frozenset({
        "rag_engine",
        "pgvector",
        "neo4j",
        "sqlite_index",
    })

    def register(self, subsystem: str, status: HealthLevel = HealthLevel.HEALTHY) -> None:
        """Explicitly register a subsystem (usually called at init)."""
        if subsystem not in self._registry:
            self._registry[subsystem] = status
        logger.debug("DegradationRegistry.register: %s → %s", subsystem, status.value)

    def degrade(self, subsystem: str, level: HealthLevel, reason: str = "") -> None:
        """
        Downgrade subsystem to *level* (monotonic — cannot improve).
        Logs a warning so the degradation is visible in the trace.
        """
        current = self._registry.get(subsystem, HealthLevel.HEALTHY)
        if level.is_worse_than(current):  # type: ignore[arg-type]
            self._registry[subsystem] = level
            self._reasons.setdefault(subsystem, []).append(reason)
            logger.warning(
                "⚠️  RunHealth: %s → %s | reason: %s",
                subsystem, level.value, reason or "(unspecified)",
            )
        else:
            # Already at same or worse — still append reason for traceability
            self._reasons.setdefault(subsystem, []).append(reason)

    def health(self) -> HealthLevel:
        """Worst health level across all registered subsystems (derived, never stored)."""
        if not self._registry:
            return HealthLevel.HEALTHY
        worst = max(self._registry.values(), key=lambda h: {
            HealthLevel.HEALTHY: 0, HealthLevel.DEGRADED: 1, HealthLevel.FAILED: 2
        }[h])
        return worst

    def has_critical_failure(self) -> bool:
        """True if any evidence-critical subsystem is FAILED."""
        for sub in self.EVIDENCE_CRITICAL:
            if self._registry.get(sub, HealthLevel.HEALTHY) == HealthLevel.FAILED:
                return True
        return False

    def snapshot(self) -> Dict[str, str]:
        """Return a serialisable dict suitable for RunRecord."""
        return {sub: lvl.value for sub, lvl in self._registry.items()}

    def reasons_snapshot(self) -> Dict[str, List[str]]:
        return dict(self._reasons)


# ============================================================================
# PHASE TIMER
# ============================================================================

@dataclass
class _PhaseTimer:
    name: str
    start: float = field(default_factory=time.monotonic)
    duration_ms: Optional[float] = None

    def stop(self) -> float:
        self.duration_ms = (time.monotonic() - self.start) * 1000
        return self.duration_ms


# ============================================================================
# RUN CONTEXT (B11 + B12)
# ============================================================================

@dataclass
class RunContext:
    """
    Per-run request-scoped container (B11 + B12).

    Carries identity, phase timers, degradation registry, and budget.
    Passed by reference; never global.

    Usage in workflow nodes::

        ctx = state.get("run_ctx")
        ctx.start_phase("evidence_collection")
        ...
        ctx.end_phase("evidence_collection")

        # charge at LLM boundary (inside finally so partials are recorded):
        try:
            response = llm.invoke(...)
        finally:
            ctx.budget.charge(tokens_in=..., tokens_out=..., cost_usd=...)
    """
    run_id:    str   = field(default_factory=lambda: str(uuid.uuid4()))
    ticket_id: str   = ""
    repo:      str   = ""
    indexed_commit:  Optional[str] = None
    current_commit:  Optional[str] = None
    final_status:    str = "RUNNING"

    # Sub-objects
    budget:   RunBudget          = field(default_factory=RunBudget)
    registry: DegradationRegistry = field(default_factory=DegradationRegistry)

    # Phase timers
    _phases: Dict[str, _PhaseTimer] = field(default_factory=dict, init=False, repr=False)

    # Accumulated stats for RunRecord
    discovery_candidates: int  = 0
    discovery_truncated:  int  = 0
    discovery_near_miss:  int  = 0
    evidence_promoted:    int  = 0
    evidence_blocked:     int  = 0
    evidence_backed:      bool = False
    grounding_confidence: float = 0.0
    architecture_chunks:  int  = 0
    context_sources_used: List[str] = field(default_factory=list)
    generation_truncations: int = 0
    validation_passed:    Optional[bool] = None
    validation_level:     Optional[str]  = None
    retries:              int  = 0
    hard_stop:            bool = False

    # ── Phase timer API ───────────────────────────────────────────────────────

    def start_phase(self, name: str) -> None:
        self._phases[name] = _PhaseTimer(name)
        logger.debug("RunContext.start_phase: %s", name)

    def end_phase(self, name: str) -> float:
        """Stop timer for *name*. Returns duration in ms. Safe to call even if start missed."""
        timer = self._phases.get(name)
        if timer is None:
            logger.debug("RunContext.end_phase: '%s' had no start — skipped", name)
            return 0.0
        ms = timer.stop()
        logger.debug("RunContext.end_phase: %s = %.1f ms", name, ms)
        return ms

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Context manager that auto-ends the phase in finally."""
        self.start_phase(name)
        try:
            yield
        finally:
            self.end_phase(name)

    # ── Convenience delegations ───────────────────────────────────────────────

    def degrade(self, subsystem: str, level: HealthLevel, reason: str = "") -> None:
        self.registry.degrade(subsystem, level, reason)

    def health(self) -> HealthLevel:
        return self.registry.health()

    def check_budget(self) -> None:
        """Raise BudgetExceeded if wall-clock limit exceeded. Call at node boundaries."""
        self.budget.check()

    def record(self, key: str, value) -> None:
        """Generic attribute setter for one-off telemetry updates."""
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            logger.debug("RunContext.record: unknown key '%s' (ignoring)", key)

    # ── RunRecord emission ────────────────────────────────────────────────────

    def to_record(self) -> "RunRecord":
        phase_timings = {
            name: round(timer.duration_ms or 0.0)
            for name, timer in self._phases.items()
        }
        self.budget._refresh_elapsed()
        return RunRecord(
            run_id=self.run_id,
            ticket_id=self.ticket_id,
            repo=self.repo,
            indexed_commit=self.indexed_commit,
            current_commit=self.current_commit,
            final_status=self.final_status,
            run_health=self.health().value,
            degradations=self.registry.snapshot(),
            phase_timings_ms=phase_timings,
            llm_calls=self.budget.llm_calls,
            tokens_in=self.budget.tokens_in,
            tokens_out=self.budget.tokens_out,
            cost_usd=round(self.budget.cost_usd, 4),
            discovery_candidates=self.discovery_candidates,
            discovery_truncated=self.discovery_truncated,
            discovery_near_miss=self.discovery_near_miss,
            evidence_promoted=self.evidence_promoted,
            evidence_blocked=self.evidence_blocked,
            evidence_backed=self.evidence_backed,
            grounding_confidence=self.grounding_confidence,
            architecture_chunks=self.architecture_chunks,
            context_sources_used=self.context_sources_used,
            generation_truncations=self.generation_truncations,
            validation_passed=self.validation_passed,
            validation_level=self.validation_level,
            retries=self.retries,
            hard_stop=self.hard_stop,
            budget_exceeded=self.budget.budget_exceeded(),
        )


# ============================================================================
# RUN RECORD (emitted at terminal node)
# ============================================================================

@dataclass
class RunRecord:
    """
    Immutable audit record emitted at every terminal node.

    Required fields match the telemetry schema from the production readiness
    review (Part 6).  All alert conditions are documented inline.
    """
    run_id:        str
    ticket_id:     str
    repo:          str
    indexed_commit: Optional[str]
    current_commit: Optional[str]
    final_status:  str
    run_health:    str                 # HEALTHY | DEGRADED | FAILED

    degradations:  Dict[str, str]      # subsystem → HealthLevel.value
    phase_timings_ms: Dict[str, int]

    # LLM cost
    llm_calls:     int
    tokens_in:     int
    tokens_out:    int
    cost_usd:      float

    # Discovery
    discovery_candidates: int
    discovery_truncated:  int
    discovery_near_miss:  int

    # Evidence
    evidence_promoted: int
    evidence_blocked:  int
    evidence_backed:   bool           # CRITICAL ALERT: False + success → confident-wrong risk

    # Grounding
    grounding_confidence: float

    # Context
    architecture_chunks: int          # ALERT if 0
    context_sources_used: List[str]

    # Generation
    generation_truncations: int

    # Validation
    validation_passed: Optional[bool]
    validation_level:  Optional[str]  # "FULL" | "STATIC" | None

    # Retry / budget
    retries:        int
    hard_stop:      bool
    budget_exceeded: bool

    # Derived alerts (computed on to_dict())
    def alerts(self) -> List[str]:
        """Return list of golden-signal alert strings that should page."""
        a: List[str] = []
        if self.run_health == HealthLevel.FAILED.value:
            a.append("run_health=FAILED")
        if self.architecture_chunks == 0:
            a.append("architecture_chunks=0")
        if not self.evidence_backed and self.final_status == "COMPLETE":
            a.append("CRITICAL: evidence_backed=False with status=COMPLETE (confident-wrong risk)")
        if self.budget_exceeded:
            a.append("budget_exceeded")
        return a

    def to_dict(self) -> dict:
        import dataclasses
        d = dataclasses.asdict(self)
        d["alerts"] = self.alerts()
        return d


import contextvars
current_run_context: contextvars.ContextVar[Optional[RunContext]] = contextvars.ContextVar("current_run_context", default=None)
