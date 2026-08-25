"""Pydantic models for the ticket solver pipeline."""

from __future__ import annotations

import hashlib
import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TicketComplexity(str, Enum):
    """Estimated complexity of a ticket — drives gate thresholds."""
    CONFIG_CHANGE = "config_change"
    SINGLE_FILE_FIX = "single_file_fix"
    MULTI_FILE_FIX = "multi_file_fix"
    UI_FIX = "ui_fix"
    FEATURE = "feature"


class PipelineStage(str, Enum):
    """Stage in the solver pipeline."""
    INTAKE = "intake"
    PLANNING = "planning"
    EXECUTING = "executing"
    REASONING = "reasoning"
    PATCHING = "patching"
    BUILDING = "building"
    VERIFYING = "verifying"
    REPORTING = "reporting"
    ESCALATED = "escalated"
    DONE = "done"


# ---------------------------------------------------------------------------
# Input Models
# ---------------------------------------------------------------------------

class TicketInput(BaseModel):
    """A ticket to be solved."""
    ticket_id: str
    title: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    priority: str = "medium"


# ---------------------------------------------------------------------------
# Planner Output
# ---------------------------------------------------------------------------

class SkillBlockCall(BaseModel):
    """A single skill block invocation in the execution plan."""
    skill: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(
        default_factory=list,
        description="References to previous step results (e.g., 'FROM_GREP_RESULTS')"
    )


class SuccessCriterion(BaseModel):
    """A verifiable success criterion for the ticket."""
    id: str
    description: str
    verification_type: str = "build"  # build, grep_absent, grep_present, value_check, consistency
    verification_args: dict[str, Any] = Field(default_factory=dict)


class AlternativeApproach(BaseModel):
    """An alternative solution approach the Planner identified."""
    approach: str
    pros: str
    cons: str


class ExecutionPlan(BaseModel):
    """Output of Model 1 (Planner) — structured execution plan."""
    ticket_id: str
    understanding: str
    assumptions: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    alternative_approaches: list[AlternativeApproach] = Field(default_factory=list)
    recommended_approach: int = 0
    recommended_approach_reason: str = ""
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    ticket_complexity: TicketComplexity = TicketComplexity.SINGLE_FILE_FIX
    plan: list[SkillBlockCall] = Field(default_factory=list)
    reasoning_hints: list[str] = Field(default_factory=list)
    target_services: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Context Models
# ---------------------------------------------------------------------------

class FileContext(BaseModel):
    """Content of a single file gathered by the executor."""
    path: str
    content: str
    start_line: int = 1
    end_line: int = 0
    is_full_file: bool = True


class GatheredContext(BaseModel):
    """All context gathered by the Executor from running Skill Blocks."""
    files: list[FileContext] = Field(default_factory=list)
    symbols: list[dict[str, Any]] = Field(default_factory=list)
    grep_matches: list[dict[str, Any]] = Field(default_factory=list)
    dependency_chain: list[str] = Field(default_factory=list)

    def estimate_tokens(self) -> int:
        """Rough token estimate (1 token ≈ 4 chars)."""
        total_chars = sum(len(f.content) for f in self.files)
        total_chars += sum(len(str(s)) for s in self.symbols)
        total_chars += sum(len(str(g)) for g in self.grep_matches)
        return total_chars // 4

    def compress(self, max_tokens: int = 15_000) -> "GatheredContext":
        """Trim files to fit within token budget, keeping the most relevant."""
        if self.estimate_tokens() <= max_tokens:
            return self
        # Sort by file size ascending — keep smaller (more focused) files
        sorted_files = sorted(self.files, key=lambda f: len(f.content))
        kept: list[FileContext] = []
        budget = max_tokens * 4  # chars
        for f in sorted_files:
            if budget - len(f.content) > 0:
                kept.append(f)
                budget -= len(f.content)
            else:
                # Truncate the last file to fit
                kept.append(f.model_copy(update={
                    "content": f.content[:budget],
                    "is_full_file": False
                }))
                break
        return self.model_copy(update={"files": kept})


# ---------------------------------------------------------------------------
# Patch Models
# ---------------------------------------------------------------------------

class PatchHunk(BaseModel):
    """A single hunk within a patch."""
    start_line: int
    end_line: int
    original: str
    modified: str

    def is_whitespace_only(self) -> bool:
        """Check if this hunk only changes whitespace."""
        return self.original.strip() == self.modified.strip()

    def is_comment_only_change(self) -> bool:
        """Check if this hunk only changes comments."""
        orig_lines = [l for l in self.original.splitlines() if l.strip() and not l.strip().startswith(("//", "/*", "*", "#"))]
        mod_lines = [l for l in self.modified.splitlines() if l.strip() and not l.strip().startswith(("//", "/*", "*", "#"))]
        return orig_lines == mod_lines

    def touches_logic(self) -> bool:
        """Check if any logic lines were modified."""
        return not self.is_comment_only_change()


class Patch(BaseModel):
    """A code patch for a single file."""
    file_path: str
    hunks: list[PatchHunk] = Field(default_factory=list)
    is_new_file: bool = False
    full_content: Optional[str] = None  # For new files or full replacements

    @property
    def lines_added(self) -> int:
        return sum(len(h.modified.splitlines()) for h in self.hunks)

    @property
    def lines_removed(self) -> int:
        return sum(len(h.original.splitlines()) for h in self.hunks)


# ---------------------------------------------------------------------------
# Reasoner Output
# ---------------------------------------------------------------------------

class CriterionVerification(BaseModel):
    """Verification result for a single success criterion."""
    criterion_id: str
    passed: bool
    reason: str = ""


class ReasonerOutput(BaseModel):
    """Output of Model 2 (Reasoner) — code patches + explanations."""
    root_cause: str
    explanation: str
    patches: list[Patch] = Field(default_factory=list)
    verifications: dict[str, CriterionVerification] = Field(default_factory=dict)
    confidence: float = 0.0
    target_files: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Recovery Models
# ---------------------------------------------------------------------------

class RecoveryDecision(BaseModel):
    """Formal contract for every retry attempt."""
    failure_fingerprint: str
    attempt_number: int
    strategy_history: list[str] = Field(default_factory=list)
    next_strategy: str
    next_strategy_rationale: str
    stop_condition: str


class BuildError(BaseModel):
    """Structured build error."""
    error_type: str  # compilation, test_failure, dependency, runtime
    file: str = ""
    line: int = 0
    message: str = ""
    symbol: str = ""  # The class/method/variable involved

    @property
    def fingerprint(self) -> str:
        """Normalized fingerprint: error_class + symbol + message pattern."""
        normalized_msg = self.message[:100].lower().strip()
        raw = f"{self.error_type}:{self.symbol}:{normalized_msg}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Pipeline State
# ---------------------------------------------------------------------------

class PipelineState(BaseModel):
    """Full state of the ticket solver pipeline."""
    ticket: TicketInput
    stage: PipelineStage = PipelineStage.INTAKE
    plan: Optional[ExecutionPlan] = None
    context: Optional[GatheredContext] = None
    reasoner_output: Optional[ReasonerOutput] = None
    build_errors: list[BuildError] = Field(default_factory=list)
    recovery_history: list[RecoveryDecision] = Field(default_factory=list)
    report: str = ""
    gate_logs: list[dict[str, Any]] = Field(default_factory=list)
    start_time: float = Field(default_factory=time.time)
