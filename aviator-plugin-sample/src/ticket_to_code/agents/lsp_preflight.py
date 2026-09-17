"""
LSP / Compiler Preflight — bounded, diagnostic-driven decision BEFORE build.

This is NOT a new agent, repair loop, generation path, or error-resolution
architecture. It COMPOSES the already-existing primitives into one deterministic
decision the flow can consult after generation / patch-gate and before the
expensive build:

  * build_diagnostic_classifier.classify_build_diagnostics → error attribution
      (infrastructure / pre-existing / ticket-introduced / generated / unknown)
  * edit_loop_policy.decide_edit_action                    → bounded repair action
      (targeted / stop-repeated / return-to-planning / reject-unrelated / clean)

Diagnostics are supplied by existing infrastructure (LSP/compiler/targeted
compile / GeneratedReferenceValidator); this module never runs a compiler or
invents diagnostics. It decides FIX / LEAVE+CONTINUE / STOP and, for a FIX,
returns the minimal targeted repair (affected file + symbol) for the EXISTING
bounded edit loop.

Safety rules (PART 6/7):
  * timeout / no-response  → LEAVE+CONTINUE (never authorizes source edits)
  * infrastructure/external→ LEAVE+CONTINUE (no speculative source changes)
  * pre-existing only      → LEAVE+CONTINUE (differential accept)
  * ambiguous (no file)    → LEAVE+CONTINUE (do NOT guess)
  * unrelated file         → never modified (reject)
  * repeated diagnostic    → STOP (bounded)
  * genuine new dependency → RETURN_TO_PLANNING

Language/framework/repository agnostic — no layer or filename assumptions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from ticket_to_code.agents.build_diagnostic_classifier import (
    classify_build_diagnostics,
    BuildDiagnosticReport,
)
from ticket_to_code.agents.edit_loop_policy import decide_edit_action, EditAction, error_signature


class PreflightOutcome(str, Enum):
    CLEAN = "clean"                            # no diagnostics — proceed to build
    REPAIR = "repair"                          # targeted bounded repair recommended
    LEAVE_AND_CONTINUE = "leave_and_continue"  # nothing repairable-by-us — proceed to build
    STOP = "stop"                              # repeated/bounded-exhausted — hand off, do not loop
    RETURN_TO_PLANNING = "return_to_planning"  # genuine new architectural dependency


# Extract a quoted/backticked identifier from a diagnostic (advisory hint only).
_SYMBOL_RES = (
    re.compile(r"[`'\"]([A-Za-z_$][\w$]*)[`'\"]"),
    re.compile(r"\bname ([A-Za-z_$][\w$]*)\b"),
    re.compile(r"\bmember ([A-Za-z_$][\w$]*)\b"),
    re.compile(r"\bproperty ([A-Za-z_$][\w$]*)\b", re.IGNORECASE),
)


def _symbol_from(diagnostic: str) -> str:
    for rex in _SYMBOL_RES:
        m = rex.search(diagnostic or "")
        if m:
            return m.group(1)
    return ""


@dataclass
class TargetedRepair:
    file_path: str
    diagnostic: str
    symbol: str = ""


@dataclass
class PreflightResult:
    outcome: str                                   # PreflightOutcome value
    reason: str = ""
    repairs: list = field(default_factory=list)    # TargetedRepair[]
    classified: BuildDiagnosticReport = field(default_factory=BuildDiagnosticReport)
    attribution: dict = field(default_factory=dict)  # category -> count
    signature: str = ""                            # signature of blocking diagnostics

    @property
    def should_repair(self) -> bool:
        return self.outcome == PreflightOutcome.REPAIR.value

    @property
    def proceeds_to_build(self) -> bool:
        return self.outcome in (
            PreflightOutcome.CLEAN.value, PreflightOutcome.LEAVE_AND_CONTINUE.value
        )


def _counts(report: BuildDiagnosticReport) -> dict:
    out: dict = {}
    for d in report.diagnostics:
        out[d.category] = out.get(d.category, 0) + 1
    return out


def run_preflight(
    diagnostics: list,
    generated_files: set,
    planned_files: set | None = None,
    prev_signatures: set | None = None,
    *,
    timed_out: bool = False,
    no_response: bool = False,
    new_dependency: bool = False,
) -> PreflightResult:
    """Decide the pre-build action from concrete diagnostics.

    Args:
        diagnostics: raw LSP/compiler diagnostic lines from existing infrastructure.
        generated_files: files this ticket generated/modified (change surface).
        planned_files: files the plan authorized to change (owned scope).
        prev_signatures: signatures of previously-seen blocking diagnostics (bounding).
        timed_out: the diagnostic source timed out.
        no_response: the diagnostic source returned nothing usable.
        new_dependency: a genuine new architectural dependency was detected.
    """
    planned_files = planned_files or set()
    prev_signatures = prev_signatures or set()
    generated_files = generated_files or set()

    # PART 6: timeout / no-response must NEVER authorize source edits.
    if timed_out or no_response:
        return PreflightResult(
            outcome=PreflightOutcome.LEAVE_AND_CONTINUE.value,
            reason="preflight unavailable (timeout/no-response) — leave and continue, no speculative edits",
        )

    classified = classify_build_diagnostics(list(diagnostics or []), set(generated_files))
    attribution = _counts(classified)

    # No blocking (repairable-by-us) diagnostics → proceed to build.
    if not classified.blocking:
        if not classified.diagnostics:
            return PreflightResult(PreflightOutcome.CLEAN.value, "no diagnostics",
                                   classified=classified, attribution=attribution)
        if classified.is_infrastructure_only:
            return PreflightResult(PreflightOutcome.LEAVE_AND_CONTINUE.value,
                                   "infrastructure/external only — no source repair",
                                   classified=classified, attribution=attribution)
        return PreflightResult(PreflightOutcome.LEAVE_AND_CONTINUE.value,
                               "only pre-existing diagnostics — differential accept",
                               classified=classified, attribution=attribution)

    # Blocking diagnostics: decide the bounded action per affected file.
    blocking = classified.blocking
    signature = error_signature([d.raw for d in blocking])
    repairs: list = []
    actions: set = set()
    ambiguous = 0

    for d in blocking:
        fp = d.file_path or ""
        if not fp:
            # PART 6: ambiguous (no attributable file) → do NOT guess.
            ambiguous += 1
            continue
        action = decide_edit_action(
            [d.raw], fp, set(generated_files), set(planned_files),
            set(prev_signatures), new_dependency=new_dependency,
        )
        actions.add(action)
        if action == EditAction.TARGETED_REPAIR:
            repairs.append(TargetedRepair(file_path=fp, diagnostic=d.raw, symbol=_symbol_from(d.raw)))

    # Precedence: new dependency → replan; concrete repair; repeated → stop; else leave.
    if EditAction.RETURN_TO_PLANNING in actions:
        return PreflightResult(PreflightOutcome.RETURN_TO_PLANNING.value,
                               "genuine new architectural dependency — return to planning",
                               classified=classified, attribution=attribution, signature=signature)
    if repairs:
        return PreflightResult(PreflightOutcome.REPAIR.value,
                               "targeted bounded repair of generated diagnostics",
                               repairs=repairs, classified=classified,
                               attribution=attribution, signature=signature)
    if EditAction.STOP_REPEATED in actions:
        return PreflightResult(PreflightOutcome.STOP.value,
                               "repeated diagnostic — stop bounded repair (hand off)",
                               classified=classified, attribution=attribution, signature=signature)
    # Only unrelated (rejected) and/or ambiguous remained → do not modify anything.
    reason = ("diagnostics only in unrelated files — do not modify" if not ambiguous
              else "ambiguous diagnostics — do not guess")
    return PreflightResult(PreflightOutcome.LEAVE_AND_CONTINUE.value, reason,
                           classified=classified, attribution=attribution, signature=signature)
