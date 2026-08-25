"""Orchestrator — Main ticket-solving pipeline.

Runs the full flow: Ticket → Plan → Execute → Reason → Patch → Build → Report
With Karpathy gates at each transition and budget tracking throughout.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from aviator_core.ticket_solver.budget import TicketBudget
from aviator_core.ticket_solver.executor import Executor
from aviator_core.ticket_solver.gates import GateMode
from aviator_core.ticket_solver.gates.goal_driven import GoalDrivenGate
from aviator_core.ticket_solver.gates.simplicity_first import SimplicityGate
from aviator_core.ticket_solver.gates.surgical_changes import SurgicalChangesGate
from aviator_core.ticket_solver.gates.think_before_coding import ThinkBeforeCodingGate
from aviator_core.ticket_solver.models import (
    PipelineState,
    PipelineStage,
    TicketInput,
)
from aviator_core.ticket_solver.planner import Planner
from aviator_core.ticket_solver.reasoner import Reasoner
from aviator_core.ticket_solver.recovery_engine import RecoveryEngine
from aviator_core.ticket_solver.reporter import Reporter
from aviator_core.ticket_solver.skill_blocks import SkillRegistry

logger = logging.getLogger(__name__)


class TicketSolverOrchestrator:
    """Orchestrates the full ticket-solving pipeline.

    Role-to-model mapping (per Codex caution #2):
        planner_llm  → BASE model (cheap)   → gemini-2.5-flash-lite
        reasoner_llm → ASSISTANT model (strong) → gemini-2.5-flash
        reporter_llm → BASE model (cheap)   → gemini-2.5-flash-lite
    """

    def __init__(
        self,
        planner_llm: BaseChatModel,
        reasoner_llm: BaseChatModel,
        reporter_llm: BaseChatModel,
        skill_registry: SkillRegistry | None = None,
        gate_mode: GateMode = GateMode.ADVISORY,
    ):
        # Models
        self.planner = Planner(planner_llm)
        self.reasoner = Reasoner(reasoner_llm)
        self.reporter = Reporter(reporter_llm)

        # Executor
        self.executor = Executor(skill_registry)

        # Gates (all start in the configured mode)
        self.think_gate = ThinkBeforeCodingGate(mode=gate_mode)
        self.simplicity_gate = SimplicityGate(mode=gate_mode)
        self.surgical_gate = SurgicalChangesGate(mode=gate_mode)
        self.goal_gate = GoalDrivenGate(mode=gate_mode)

        # Recovery
        self.recovery = RecoveryEngine()

    def solve(self, ticket: TicketInput) -> dict[str, Any]:
        """Run the full pipeline for a ticket.

        Returns a dict with:
            - status: "success" | "escalated" | "error"
            - report: markdown walkthrough (if success)
            - state: full PipelineState
            - budget: budget summary
            - gate_logs: all gate results
        """
        budget = TicketBudget()
        state = PipelineState(ticket=ticket)

        try:
            # ── Stage 1: PLANNING ──────────────────────────────────────
            state.stage = PipelineStage.PLANNING
            logger.info("═══ STAGE 1: PLANNING (ticket=%s) ═══", ticket.ticket_id)

            if not budget.can_call_planner():
                return self._escalate(state, budget, "Planner budget exhausted")

            plan, tokens = self.planner.generate_plan(ticket)
            budget.record_planner_call(tokens)
            state.plan = plan

            # Gate: Think Before Coding
            think_result = self.think_gate.validate(ticket, plan)
            state.gate_logs.append(think_result.to_log_entry())

            if plan.needs_clarification:
                logger.info("Planner needs clarification: %s", plan.clarification_questions)
                return {
                    "status": "needs_clarification",
                    "questions": plan.clarification_questions,
                    "state": state,
                    "budget": budget.summary(),
                    "gate_logs": state.gate_logs,
                }

            if think_result.should_block():
                logger.warning("Think gate blocked — issues: %s", think_result.issues)
                return self._escalate(state, budget, f"Think gate: {think_result.issues}")

            # Gate: Goal-Driven (plan has success criteria?)
            goal_plan_result = self.goal_gate.validate_plan(plan)
            state.gate_logs.append(goal_plan_result.to_log_entry())

            if goal_plan_result.should_block():
                logger.warning("Goal gate (plan) blocked — no success criteria")
                return self._escalate(state, budget, f"Goal gate: {goal_plan_result.issues}")

            # ── Stage 2: EXECUTING ─────────────────────────────────────
            state.stage = PipelineStage.EXECUTING
            logger.info("═══ STAGE 2: EXECUTING (%d skill blocks) ═══", len(plan.plan))

            context = self.executor.execute_plan(plan, budget)
            state.context = context
            logger.info(
                "Executor gathered: %d files, %d grep matches, ~%d tokens",
                len(context.files), len(context.grep_matches), context.estimate_tokens(),
            )

            # ── Stage 3: REASONING (with retry loop) ───────────────────
            state.stage = PipelineStage.REASONING
            build_errors_str = ""

            while budget.can_call_reasoner():
                logger.info("═══ STAGE 3: REASONING (attempt %d) ═══", budget.reasoner_calls_used + 1)

                reasoner_output, tokens = self.reasoner.generate_patches(
                    ticket, plan, context, build_errors=build_errors_str,
                )
                budget.record_reasoner_call(tokens)
                state.reasoner_output = reasoner_output

                # Gate: Simplicity First
                if reasoner_output.patches:
                    primary_service = plan.target_services[0] if plan.target_services else ""
                    simplicity_result = self.simplicity_gate.validate(
                        reasoner_output.patches, plan.ticket_complexity, primary_service,
                    )
                    state.gate_logs.append(simplicity_result.to_log_entry())

                    if simplicity_result.should_block():
                        logger.warning("Simplicity gate blocked: %s", simplicity_result.issues)
                        continue  # Retry with same context

                    # Gate: Surgical Changes
                    surgical_result = self.surgical_gate.validate(
                        reasoner_output.patches,
                        reasoner_output.target_files,
                        context.dependency_chain,
                    )
                    state.gate_logs.append(surgical_result.to_log_entry())

                    if surgical_result.should_block():
                        logger.warning("Surgical gate blocked: %s", surgical_result.issues)
                        continue

                    # Gate: Goal-Driven (all criteria verified?)
                    goal_result = self.goal_gate.validate_result(plan, reasoner_output)
                    state.gate_logs.append(goal_result.to_log_entry())

                    if goal_result.should_block():
                        logger.warning("Goal gate (result) blocked: %s", goal_result.issues)
                        continue

                # ── Stage 4: BUILD ─────────────────────────────────────
                state.stage = PipelineStage.BUILDING
                logger.info("═══ STAGE 4: BUILD ═══")

                # For Phase 1, we log what would be built but don't actually build.
                # Build runner skill block will be added in Phase 2.
                build_result = "Build check skipped (Phase 1 — no build runner yet)"
                build_passed = True  # Assume pass for now

                if build_passed:
                    break  # Success — move to reporting
                else:
                    # Recovery decision
                    if not self.recovery.should_retry(state.recovery_history):
                        return self._escalate(
                            state, budget, "Recovery: retry oscillation detected"
                        )
                    # Feed errors back to reasoner on next loop iteration
                    build_errors_str = build_result

            else:
                # Budget exhausted without a successful build
                return self._escalate(state, budget, "Reasoner budget exhausted")

            # ── Stage 5: REPORTING ─────────────────────────────────────
            state.stage = PipelineStage.REPORTING
            logger.info("═══ STAGE 5: REPORTING ═══")

            if budget.can_call_reporter() and state.reasoner_output:
                report, tokens = self.reporter.generate_report(
                    ticket, plan, state.reasoner_output, build_result,
                )
                budget.record_reporter_call(tokens)
                state.report = report

            state.stage = PipelineStage.DONE
            logger.info("═══ DONE (ticket=%s) ═══", ticket.ticket_id)

            return {
                "status": "success",
                "report": state.report,
                "patches": [
                    {"file": p.file_path, "hunks": len(p.hunks)}
                    for p in (state.reasoner_output.patches if state.reasoner_output else [])
                ],
                "state": state,
                "budget": budget.summary(),
                "gate_logs": state.gate_logs,
            }

        except Exception as e:
            logger.error("Pipeline error: %s", e, exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "state": state,
                "budget": budget.summary(),
                "gate_logs": state.gate_logs,
            }

    def _escalate(
        self,
        state: PipelineState,
        budget: TicketBudget,
        reason: str,
    ) -> dict[str, Any]:
        state.stage = PipelineStage.ESCALATED
        logger.warning("ESCALATED: %s", reason)
        return {
            "status": "escalated",
            "reason": reason,
            "state": state,
            "budget": budget.summary(),
            "gate_logs": state.gate_logs,
        }


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def create_orchestrator(gate_mode: GateMode = GateMode.ADVISORY) -> TicketSolverOrchestrator:
    """Create an orchestrator using the LLMRegistry from aviator_adt.

    This wires the role-to-model mapping:
        planner  → LLMRegistry.get_llm(assistant=False) → gemini-2.5-flash-lite
        reasoner → LLMRegistry.get_llm(assistant=True)  → gemini-2.5-flash
        reporter → LLMRegistry.get_llm(assistant=False) → gemini-2.5-flash-lite
    """
    # Import here to avoid circular dependency with aviator_adt
    from aviator.services.llm import LLMRegistry

    planner_llm = LLMRegistry.get_llm(assistant=False)
    reasoner_llm = LLMRegistry.get_llm(assistant=True)
    reporter_llm = LLMRegistry.get_llm(assistant=False)

    return TicketSolverOrchestrator(
        planner_llm=planner_llm,
        reasoner_llm=reasoner_llm,
        reporter_llm=reporter_llm,
        gate_mode=gate_mode,
    )
