"""Karpathy quality gates — base classes and shared types."""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class GateMode(str, Enum):
    """Gate enforcement level — progresses during calibration."""
    ADVISORY = "advisory"        # Log warning, never block
    CALIBRATED = "calibrated"    # Soft-block, user can override
    ENFORCED = "enforced"        # Hard-block, must retry


class GateResult(BaseModel):
    """Result of running a quality gate."""
    gate_name: str
    passed: bool
    issues: list[str] = Field(default_factory=list)
    mode: GateMode = GateMode.ADVISORY
    timestamp: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def should_block(self) -> bool:
        """Whether this result should block pipeline progress."""
        if self.passed:
            return False
        if self.mode == GateMode.ADVISORY:
            return False  # Never block — just log
        return True  # CALIBRATED and ENFORCED both block

    def to_log_entry(self) -> dict[str, Any]:
        """Convert to a telemetry log entry."""
        return {
            "gate": self.gate_name,
            "passed": self.passed,
            "mode": self.mode.value,
            "blocked": self.should_block(),
            "issues": self.issues,
            "timestamp": self.timestamp,
            **self.metadata,
        }


class BaseGate:
    """Base class for all Karpathy quality gates."""

    gate_name: str = "base"

    def __init__(self, mode: GateMode = GateMode.ADVISORY):
        self.mode = mode

    def _make_result(self, issues: list[str], **metadata: Any) -> GateResult:
        """Create a GateResult and log it."""
        result = GateResult(
            gate_name=self.gate_name,
            passed=len(issues) == 0,
            issues=issues,
            mode=self.mode,
            metadata=metadata,
        )

        if result.issues:
            log_fn = logger.warning if result.should_block() else logger.info
            log_fn(
                "Gate [%s] mode=%s passed=%s issues=%s",
                self.gate_name, self.mode.value, result.passed, result.issues,
            )

        return result
