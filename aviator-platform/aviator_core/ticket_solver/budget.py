"""Per-ticket budget tracking — prevents runaway costs."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TicketBudget:
    """Hard limits per ticket to prevent runaway token/time costs.

    All mutable defaults use default_factory to avoid shared-state bugs.
    """

    # Limits
    max_planner_calls: int = 2
    max_reasoner_calls: int = 3
    max_reporter_calls: int = 1
    max_total_llm_calls: int = 6
    max_total_tokens: int = 50_000
    max_runtime_seconds: int = 300  # 5 minutes
    max_skill_block_calls: int = 30

    # Tracking — all start at zero
    planner_calls_used: int = 0
    reasoner_calls_used: int = 0
    reporter_calls_used: int = 0
    llm_calls_used: int = 0
    tokens_used: int = 0
    skill_calls_used: int = 0
    start_time: float = field(default_factory=time.time)

    def can_call_planner(self) -> bool:
        return (
            self.planner_calls_used < self.max_planner_calls
            and self._within_global_limits()
        )

    def can_call_reasoner(self) -> bool:
        return (
            self.reasoner_calls_used < self.max_reasoner_calls
            and self._within_global_limits()
        )

    def can_call_reporter(self) -> bool:
        return (
            self.reporter_calls_used < self.max_reporter_calls
            and self._within_global_limits()
        )

    def can_call_skill(self) -> bool:
        return self.skill_calls_used < self.max_skill_block_calls

    def _within_global_limits(self) -> bool:
        return (
            self.llm_calls_used < self.max_total_llm_calls
            and self.tokens_used < self.max_total_tokens
            and time.time() - self.start_time < self.max_runtime_seconds
        )

    def record_planner_call(self, tokens: int) -> None:
        self.planner_calls_used += 1
        self.llm_calls_used += 1
        self.tokens_used += tokens
        logger.info(
            "Budget: planner call #%d, total LLM=%d/%d, tokens=%d/%d",
            self.planner_calls_used, self.llm_calls_used,
            self.max_total_llm_calls, self.tokens_used, self.max_total_tokens,
        )

    def record_reasoner_call(self, tokens: int) -> None:
        self.reasoner_calls_used += 1
        self.llm_calls_used += 1
        self.tokens_used += tokens
        logger.info(
            "Budget: reasoner call #%d, total LLM=%d/%d, tokens=%d/%d",
            self.reasoner_calls_used, self.llm_calls_used,
            self.max_total_llm_calls, self.tokens_used, self.max_total_tokens,
        )

    def record_reporter_call(self, tokens: int) -> None:
        self.reporter_calls_used += 1
        self.llm_calls_used += 1
        self.tokens_used += tokens

    def record_skill_call(self) -> None:
        self.skill_calls_used += 1

    def summary(self) -> dict:
        elapsed = time.time() - self.start_time
        return {
            "llm_calls": f"{self.llm_calls_used}/{self.max_total_llm_calls}",
            "tokens": f"{self.tokens_used}/{self.max_total_tokens}",
            "skill_calls": f"{self.skill_calls_used}/{self.max_skill_block_calls}",
            "runtime_seconds": f"{elapsed:.1f}/{self.max_runtime_seconds}",
            "budget_exhausted": not self._within_global_limits(),
        }
