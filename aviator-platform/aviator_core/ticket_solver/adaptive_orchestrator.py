"""Adaptive Orchestrator — replaces the static pipeline with a dynamic reasoning loop.

OLD (static):   Planner → Execute ALL steps → Reasoner → Build → Report
NEW (adaptive):  Policy → ONE Action → Observe → Evidence → Update Belief → Choose Next → ...

Key differences:
1. BeliefState is updated after EVERY action (not assembled at the end)
2. Policy decides what to do next based on current understanding (not a fixed plan)
3. Evidence Layer normalizes raw observations into scored claims
4. Recovery happens at every step (not just after build failures)
5. Gates feed back into policy (repeated violations → constrain scope)
6. Experience is saved after every ticket for cross-ticket learning
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from aviator_core.ticket_solver.budget import TicketBudget
from aviator_core.ticket_solver.evidence_layer import EvidenceExtractor, TheoryManager
from aviator_core.ticket_solver.experience_store import ExperienceStore
from aviator_core.ticket_solver.gates import GateMode, GateResult
from aviator_core.ticket_solver.gates.goal_driven import GoalDrivenGate
from aviator_core.ticket_solver.gates.simplicity_first import SimplicityGate
from aviator_core.ticket_solver.gates.surgical_changes import SurgicalChangesGate
from aviator_core.ticket_solver.gates.think_before_coding import ThinkBeforeCodingGate
from aviator_core.ticket_solver.models import (
    ExecutionPlan,
    ReasonerOutput,
    TicketComplexity,
    TicketInput,
)
from aviator_core.ticket_solver.policy_engine import PolicyEngine
from aviator_core.ticket_solver.reasoner import Reasoner
from aviator_core.ticket_solver.reasoning import (
    ActionType,
    BeliefState,
    Observation,
    ObservationType,
    PolicyDecision,
    Theory,
    TheoryStatus,
)
from aviator_core.ticket_solver.reporter import Reporter
from aviator_core.ticket_solver.skill_blocks import SkillRegistry

logger = logging.getLogger(__name__)


class AdaptiveOrchestrator:
    """The real reasoning loop — Policy → Action → Observe → Update → Choose.

    Architecture:

        ┌─────────────────────────────────────────────────────────┐
        │  for each step until done/budget/escalate:              │
        │                                                         │
        │  1. Policy reads BeliefState → decides next action      │
        │  2. Execute ONE action (skill block)                    │
        │  3. Create Observation (raw result)                     │
        │  4. Evidence Layer extracts Evidence[] from Observation  │
        │  5. Theory Manager updates/proposes theories            │
        │  6. BeliefState updated with evidence + theories        │
        │  7. Gates check (advisory) → feed violations to policy  │
        │  8. Loop back to step 1                                 │
        │                                                         │
        │  Special action types:                                  │
        │  - HYPOTHESIZE → LLM forms theories from evidence       │
        │  - GENERATE_PATCH → Reasoner writes code                │
        │  - APPLY_PATCH → Apply patches to workspace             │
        │  - BUILD → run real build + create build evidence        │
        │  - REPORT → Reporter generates summary → done           │
        │  - ESCALATE → hand to human → done                      │
        └─────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        planner_llm: BaseChatModel,
        reasoner_llm: BaseChatModel,
        reporter_llm: BaseChatModel,
        skill_registry: SkillRegistry | None = None,
        gate_mode: GateMode = GateMode.ADVISORY,
        experience_store: ExperienceStore | None = None,
    ):
        # Models
        self.reasoner = Reasoner(reasoner_llm)
        self.reporter = Reporter(reporter_llm)

        # Policy engine (uses planner LLM for fallback decisions)
        self.policy = PolicyEngine(planner_llm=planner_llm, gate_mode=gate_mode)

        # Skill blocks — register all available skills including build + patch
        self.registry = skill_registry or self._create_full_registry()

        # Evidence & theory
        self.evidence_extractor = EvidenceExtractor()
        self.theory_manager = TheoryManager()

        # Gates
        self.think_gate = ThinkBeforeCodingGate(mode=gate_mode)
        self.simplicity_gate = SimplicityGate(mode=gate_mode)
        self.surgical_gate = SurgicalChangesGate(mode=gate_mode)
        self.goal_gate = GoalDrivenGate(mode=gate_mode)

        # Experience
        self.experience_store = experience_store or ExperienceStore()

        # State
        self._obs_counter = 0
        self._gate_history: list[GateResult] = []
        self._last_reasoner_output: ReasonerOutput | None = None  # For patch application
        self._patch_gen_count = 0    # Track generate→apply→build cycles
        self._patch_apply_count = 0
        self._build_count = 0

    @staticmethod
    def _create_full_registry() -> SkillRegistry:
        """Create registry with ALL skill blocks including build + patch."""
        from aviator_core.ticket_solver.skill_blocks.build_runner import BuildRunnerSkill
        from aviator_core.ticket_solver.skill_blocks.file_reader import FileReaderSkill, BatchFileReaderSkill
        from aviator_core.ticket_solver.skill_blocks.grep_codebase import GrepCodebaseSkill
        from aviator_core.ticket_solver.skill_blocks.patch_applier import PatchApplierSkill

        registry = SkillRegistry()
        registry.register(GrepCodebaseSkill())
        registry.register(FileReaderSkill())
        registry.register(BatchFileReaderSkill())
        registry.register(BuildRunnerSkill())
        registry.register(PatchApplierSkill())
        return registry

    @staticmethod
    def _infer_ticket_type(ticket: TicketInput) -> str:
        """Infer ticket complexity type from labels and description."""
        text = f"{' '.join(ticket.labels)} {ticket.title} {ticket.description}".lower()
        if any(kw in text for kw in ["config", "version", "pom", "property", "yml"]):
            return "config_change"
        if any(kw in text for kw in ["component", "angular", "css", "ui", "display", "layout"]):
            return "ui_fix"
        if any(kw in text for kw in ["feature", "new endpoint", "add support", "implement"]):
            return "feature"
        service_count = sum(
            1 for svc in ["area", "project", "sagas", "deliverable", "transmittal", "gateway"]
            if svc in text
        )
        if service_count > 1:
            return "multi_file_fix"
        return "single_file_fix"

    def solve(self, ticket: TicketInput) -> dict[str, Any]:
        """Run the adaptive reasoning loop for a ticket.

        Returns:
            - status: "success" | "escalated" | "needs_clarification" | "error"
            - report: markdown walkthrough (if success)
            - belief: final BeliefState
            - budget: budget summary
            - gate_logs: all gate results
        """
        budget = TicketBudget()
        belief = BeliefState()
        self._obs_counter = 0
        self._gate_history = []

        # Seed initial theories from ticket text
        initial_theories = self.theory_manager.create_initial_theories(
            ticket.title, ticket.description,
        )
        for theory in initial_theories:
            belief.add_theory(theory)

        # Infer ticket type for experience lookup
        ticket_type = self._infer_ticket_type(ticket)

        # Load experience priors and seed belief with learnings
        priors = self.experience_store.get_winning_patterns(ticket_type)
        if priors.get("has_priors"):
            logger.info(
                "Loaded experience priors for '%s': %d similar tickets, avg %.0f steps",
                ticket_type, priors["sample_size"], priors["avg_steps"],
            )
            # Seed belief with prior learnings
            common_actions = [a for a, _ in priors.get("common_actions", [])]
            if common_actions:
                belief.next_best_actions = common_actions[:3]
                belief.missing_info.append(
                    f"Prior experience suggests: start with {common_actions[0]}"
                )
            common_services = priors.get("common_services", [])
            if common_services:
                belief.prior_services = common_services[:2]

        # Track for final reporting
        reasoner_output: ReasonerOutput | None = None
        plan: ExecutionPlan | None = None  # Built on-the-fly if needed

        try:
            # ══════════════════════════════════════════════════════════
            # THE LOOP: Policy → Action → Observe → Evidence → Believe
            # ══════════════════════════════════════════════════════════
            while True:
                # ── Policy decides ───────────────────────────────────
                decision = self.policy.decide_next(
                    ticket, belief, budget, self._gate_history,
                )

                logger.info(
                    "Step %d: action=%s rationale='%s'",
                    belief.step_count + 1,
                    decision.action_name,
                    decision.rationale[:80],
                )

                # Drain LLM policy token usage if present
                policy_tokens = decision.action_args.pop("_policy_tokens", 0)
                if policy_tokens:
                    budget.record_planner_call(policy_tokens)

                # ── Terminal actions ─────────────────────────────────
                if decision.is_terminal:
                    if decision.action_type == ActionType.ESCALATE:
                        return self._finish(
                            "escalated", ticket, belief, budget,
                            reason=decision.rationale,
                        )
                    if decision.action_type == ActionType.REPORT:
                        if reasoner_output and budget.can_call_reporter():
                            # Build a minimal plan for the reporter
                            plan = plan or self._build_plan_from_belief(ticket, belief)
                            report, tokens = self.reporter.generate_report(
                                ticket, plan, reasoner_output,
                                build_result="Build check: see evidence",
                            )
                            budget.record_reporter_call(tokens)
                            return self._finish(
                                "success", ticket, belief, budget,
                                report=report, reasoner_output=reasoner_output,
                            )
                        return self._finish(
                            "success", ticket, belief, budget,
                            report=f"## Resolved\n{belief.to_context_string()}",
                        )

                # ── ASK USER ─────────────────────────────────────────
                if decision.action_type == ActionType.ASK_USER:
                    return self._finish(
                        "needs_clarification", ticket, belief, budget,
                        questions=[decision.rationale],
                    )

                # ── HYPOTHESIZE (LLM forms theories) ─────────────────
                if decision.action_type == ActionType.HYPOTHESIZE:
                    obs = self._hypothesize(ticket, belief, budget)
                    belief.add_observation(obs)
                    if not obs.error:
                        evidence = self.evidence_extractor.extract(obs)
                        self.theory_manager.update_with_evidence(belief, evidence)
                    continue

                # ── GENERATE PATCH (Reasoner) ────────────────────────
                if decision.action_type == ActionType.GENERATE_PATCH:
                    plan = self._build_plan_from_belief(ticket, belief)
                    context = self._build_context_from_belief(belief)

                    output, tokens = self.reasoner.generate_patches(
                        ticket, plan, context,
                    )
                    budget.record_reasoner_call(tokens)
                    reasoner_output = output
                    self._last_reasoner_output = output  # For _apply_patches
                    self._patch_gen_count += 1

                    # Run gates on the patches
                    gate_results = self._run_patch_gates(
                        output, plan, belief,
                    )
                    self._gate_history.extend(gate_results)

                    # Record as observation
                    obs = Observation(
                        id=self._next_obs_id(),
                        action_name="generate_patch",
                        observation_type=ObservationType.LLM_RESPONSE,
                        raw_data={
                            "patches": len(output.patches),
                            "confidence": output.confidence,
                            "root_cause": output.root_cause,
                        },
                    )
                    belief.add_observation(obs)

                    # Update confidence from reasoner
                    if output.confidence >= 0.7:
                        belief.overall_confidence = max(
                            belief.overall_confidence, output.confidence,
                        )

                    # If gates all passed → boost confidence to trigger report
                    if all(r.passed for r in gate_results):
                        belief.overall_confidence = max(belief.overall_confidence, 0.9)

                    continue

                # ── APPLY PATCH ───────────────────────────────────────
                if decision.action_type == ActionType.APPLY_PATCH:
                    obs = self._apply_patches(belief, budget)
                    belief.add_observation(obs)
                    if not obs.error:
                        evidence = self.evidence_extractor.extract(obs)
                        self.theory_manager.update_with_evidence(belief, evidence)
                    self._patch_apply_count += 1
                    continue

                # ── BUILD (real build runner) ─────────────────────────
                if decision.action_type == ActionType.BUILD:
                    obs = self._run_build(belief, budget)
                    belief.add_observation(obs)
                    evidence = self.evidence_extractor.extract(obs)
                    self.theory_manager.update_with_evidence(belief, evidence)
                    self._build_count += 1

                    # If build failed, clear contradictions flag for retry
                    if obs.raw_data.get("passed") is False:
                        # Feed errors back — policy will re-enter GENERATE_PATCH
                        errors = obs.raw_data.get("errors", [])
                        for err in errors:
                            belief.missing_info.append(
                                f"Build error: {err.get('message', '')[:100]}"
                            )
                        belief.overall_confidence = max(0.3, belief.overall_confidence - 0.3)
                    continue

                # ── SEARCH / READ / QUERY (skill blocks) ─────────────
                obs = self._execute_skill(decision, budget)
                belief.add_observation(obs)

                if not obs.error and not obs.is_empty:
                    # Extract evidence
                    evidence = self.evidence_extractor.extract(obs)
                    self.theory_manager.update_with_evidence(belief, evidence)

                    # Try to propose new theories from accumulated evidence
                    self.theory_manager.propose_theory_from_evidence(
                        belief, evidence,
                    )
                else:
                    # Step-level recovery: action failed/empty → policy will react
                    if obs.is_empty:
                        belief.missing_info.append(
                            f"Search '{decision.action_args.get('pattern', '?')}' found nothing"
                        )

                logger.info(
                    "BeliefState: confidence=%.0f%% theories=%d evidence=%d owners=%d",
                    belief.overall_confidence * 100,
                    len(belief.theories),
                    len(belief.evidence),
                    len(belief.owner_candidates),
                )

        except Exception as e:
            logger.error("Adaptive loop error: %s", e, exc_info=True)
            return self._finish("error", ticket, belief, budget, error=str(e))

    # ── Action executors ─────────────────────────────────────────────

    def _execute_skill(
        self,
        decision: PolicyDecision,
        budget: TicketBudget,
    ) -> Observation:
        """Execute a single skill block and wrap result as Observation."""
        if not budget.can_call_skill():
            return Observation(
                id=self._next_obs_id(),
                action_name=decision.action_name,
                observation_type=ObservationType.GREP_RESULT,
                raw_data={},
                is_empty=True,
                error="Skill budget exhausted",
            )

        skill = self.registry.get(decision.action_name)
        if not skill:
            return Observation(
                id=self._next_obs_id(),
                action_name=decision.action_name,
                observation_type=ObservationType.GREP_RESULT,
                raw_data={},
                is_empty=True,
                error=f"Unknown skill: {decision.action_name}",
            )

        try:
            result = skill.execute(**decision.action_args)
            budget.record_skill_call()

            # Determine observation type from skill name
            obs_type = {
                "grep_codebase": ObservationType.GREP_RESULT,
                "read_file_range": ObservationType.FILE_READ,
                "read_matched_files": ObservationType.FILE_READ,
                "sqlite_symbol_search": ObservationType.SYMBOL_SEARCH,
                "find_callers": ObservationType.CALLER_SEARCH,
            }.get(decision.action_name, ObservationType.GREP_RESULT)

            is_empty = not result or (
                not result.get("grep_matches") and
                not result.get("files") and
                not result.get("symbols")
            )

            return Observation(
                id=self._next_obs_id(),
                action_name=decision.action_name,
                observation_type=obs_type,
                raw_data=result,
                is_empty=is_empty,
            )

        except Exception as e:
            logger.error("Skill '%s' failed: %s", decision.action_name, e)
            return Observation(
                id=self._next_obs_id(),
                action_name=decision.action_name,
                observation_type=ObservationType.GREP_RESULT,
                raw_data={},
                error=str(e),
            )

    def _apply_patches(
        self,
        belief: BeliefState,
        budget: TicketBudget,
    ) -> Observation:
        """Apply the last Reasoner's patches to the workspace.

        Converts ReasonerOutput patches into the format expected by
        PatchApplierSkill and records the result as an Observation.
        """
        if not self._last_reasoner_output or not self._last_reasoner_output.patches:
            return Observation(
                id=self._next_obs_id(),
                action_name="apply_patch",
                observation_type=ObservationType.FILE_READ,
                raw_data={},
                is_empty=True,
                error="No patches available to apply",
            )

        # Convert Patch models to dicts for the skill block
        patch_dicts = []
        for p in self._last_reasoner_output.patches:
            hunk_dicts = [
                {
                    "start_line": h.start_line,
                    "end_line": h.end_line,
                    "original": h.original,
                    "modified": h.modified,
                }
                for h in p.hunks
            ]
            patch_dicts.append({
                "file_path": p.file_path,
                "hunks": hunk_dicts,
                "is_new_file": p.is_new_file,
                "full_content": p.full_content,
            })

        skill = self.registry.get("apply_patch")
        if not skill:
            return Observation(
                id=self._next_obs_id(),
                action_name="apply_patch",
                observation_type=ObservationType.FILE_READ,
                raw_data={},
                error="PatchApplierSkill not registered",
            )

        try:
            result = skill.execute(patches=patch_dicts, create_backup=True)
            budget.record_skill_call()

            is_empty = result.get("total_applied", 0) == 0

            logger.info(
                "PatchApply: %d applied, %d failed",
                result.get("total_applied", 0),
                result.get("total_failed", 0),
            )

            return Observation(
                id=self._next_obs_id(),
                action_name="apply_patch",
                observation_type=ObservationType.FILE_READ,
                raw_data=result,
                is_empty=is_empty,
                error="" if not result.get("failed") else f"Some patches failed: {result['failed']}",
            )

        except Exception as e:
            logger.error("Patch apply failed: %s", e)
            return Observation(
                id=self._next_obs_id(),
                action_name="apply_patch",
                observation_type=ObservationType.FILE_READ,
                raw_data={},
                error=str(e),
            )

    def _run_build(
        self,
        belief: BeliefState,
        budget: TicketBudget,
    ) -> Observation:
        """Run a real build using BuildRunnerSkill.

        Infers which service to build from the owner_candidates in belief state.
        """
        # Determine which service to build from modified files
        service = self._infer_build_service(belief)

        skill = self.registry.get("build_runner")
        if not skill:
            # No fallback — fail explicitly so evidence is truthful
            logger.error("BuildRunnerSkill not registered — build cannot run")
            return Observation(
                id=self._next_obs_id(),
                action_name="build_runner",
                observation_type=ObservationType.BUILD_RESULT,
                raw_data={
                    "passed": False,
                    "errors": [{"type": "configuration", "message": "BuildRunnerSkill not registered"}],
                    "output": "Build runner not available — cannot verify compilation",
                },
            )

        try:
            result = skill.execute(service=service)
            budget.record_skill_call()

            logger.info(
                "Build: service=%s passed=%s errors=%d",
                service, result.get("passed"), len(result.get("errors", [])),
            )

            return Observation(
                id=self._next_obs_id(),
                action_name="build_runner",
                observation_type=ObservationType.BUILD_RESULT,
                raw_data=result,
            )

        except Exception as e:
            logger.error("Build failed: %s", e)
            return Observation(
                id=self._next_obs_id(),
                action_name="build_runner",
                observation_type=ObservationType.BUILD_RESULT,
                raw_data={
                    "passed": False,
                    "errors": [{"type": "runtime", "message": str(e)}],
                    "output": str(e),
                },
            )

    def _infer_build_service(self, belief: BeliefState) -> str:
        """Infer which CC4E service to build from belief state."""
        known_services = [
            "area-service", "project-service", "sagas-service",
            "deliverable-service", "transmittal-service", "email-service",
            "gateway-service", "notification-service", "xchange-ui",
        ]

        # Check owner candidates — the service is in the file path
        for file_path in belief.owner_candidates:
            for svc in known_services:
                if svc in file_path.replace("\\", "/"):
                    return svc

        # Check observations for service mentions
        for obs in belief.observations:
            if isinstance(obs.raw_data, dict):
                for key in ("file", "relative_path", "service"):
                    val = str(obs.raw_data.get(key, ""))
                    for svc in known_services:
                        if svc in val:
                            return svc

        return "area-service"  # Default fallback

    def _hypothesize(
        self,
        ticket: TicketInput,
        belief: BeliefState,
        budget: TicketBudget,
    ) -> Observation:
        """Ask the planner LLM to form theories from current evidence."""
        if not budget.can_call_planner():
            return Observation(
                id=self._next_obs_id(),
                action_name="hypothesize",
                observation_type=ObservationType.LLM_RESPONSE,
                raw_data={},
                error="Planner budget exhausted",
            )

        # Build evidence summary for LLM
        evidence_summary = "\n".join(
            f"- [{e.evidence_type.value}] {e.claim} (confidence: {e.confidence:.0%})"
            for e in belief.evidence[-15:]  # Last 15 evidence items
        )

        from langchain_core.messages import HumanMessage, SystemMessage
        try:
            response = self.policy.planner_llm.invoke([
                SystemMessage(content=(
                    "You are analyzing evidence to form theories about a bug's root cause. "
                    "Given the evidence, propose 1-3 theories. For each theory, state: "
                    "hypothesis, proposed_fix, target_files, confidence (0-1). "
                    "Output JSON array of theories."
                )),
                HumanMessage(content=(
                    f"## Ticket: {ticket.title}\n{ticket.description[:300]}\n\n"
                    f"## Evidence Gathered\n{evidence_summary}\n\n"
                    f"## Current Understanding\n{belief.to_context_string()}"
                )),
            ])
            raw = response.content if hasattr(response, "content") else str(response)
            budget.record_planner_call(len(raw) // 4)

            # Parse theories from response
            import json
            json_str = raw
            if "```json" in raw:
                start = raw.index("```json") + 7
                end = raw.index("```", start)
                json_str = raw[start:end].strip()

            theories_data = json.loads(json_str)
            if isinstance(theories_data, dict):
                theories_data = [theories_data]

            for t_data in theories_data:
                theory = Theory(
                    id=f"th_{len(belief.theories) + 1:03d}",
                    hypothesis=t_data.get("hypothesis", ""),
                    proposed_fix=t_data.get("proposed_fix", ""),
                    target_files=t_data.get("target_files", []),
                    confidence=min(1.0, float(t_data.get("confidence", 0.5))),
                    status=TheoryStatus.PROPOSED,
                )
                belief.add_theory(theory)

            return Observation(
                id=self._next_obs_id(),
                action_name="hypothesize",
                observation_type=ObservationType.LLM_RESPONSE,
                raw_data={"theories_generated": len(theories_data)},
            )

        except Exception as e:
            logger.warning("Hypothesize failed: %s", e)
            return Observation(
                id=self._next_obs_id(),
                action_name="hypothesize",
                observation_type=ObservationType.LLM_RESPONSE,
                raw_data={},
                error=str(e),
            )

    # ── Gate checks ──────────────────────────────────────────────────

    def _run_patch_gates(
        self,
        output: ReasonerOutput,
        plan: ExecutionPlan,
        belief: BeliefState,
    ) -> list[GateResult]:
        """Run all relevant gates on generated patches."""
        results: list[GateResult] = []

        if output.patches:
            primary_service = plan.target_services[0] if plan.target_services else ""

            results.append(self.simplicity_gate.validate(
                output.patches, plan.ticket_complexity, primary_service,
            ))

            results.append(self.surgical_gate.validate(
                output.patches,
                output.target_files,
                list(belief.owner_candidates.keys()),
            ))

        if plan.success_criteria:
            results.append(self.goal_gate.validate_result(plan, output))

        return results

    # ── Helpers ───────────────────────────────────────────────────────

    def _next_obs_id(self) -> str:
        self._obs_counter += 1
        return f"obs_{self._obs_counter:03d}"

    def _build_plan_from_belief(
        self,
        ticket: TicketInput,
        belief: BeliefState,
    ) -> ExecutionPlan:
        """Build a minimal ExecutionPlan from the current belief state.

        The adaptive loop doesn't use plans for execution — but the Reasoner
        and Reporter still expect a plan object for context.
        """
        return ExecutionPlan(
            ticket_id=ticket.ticket_id,
            understanding=belief.current_hypothesis,
            assumptions=[f"Theory: {t.hypothesis}" for t in belief.theories if t.status != TheoryStatus.ABANDONED],
            success_criteria=[],
            ticket_complexity=TicketComplexity.SINGLE_FILE_FIX,  # Will be refined
            target_services=[],
            reasoning_hints=[t.proposed_fix for t in belief.theories if t.proposed_fix],
        )

    def _build_context_from_belief(self, belief: BeliefState):
        """Build GatheredContext from the belief state's observations."""
        from aviator_core.ticket_solver.models import FileContext, GatheredContext

        files: list[FileContext] = []
        grep_matches: list[dict] = []

        for obs in belief.observations:
            if obs.observation_type == ObservationType.FILE_READ and not obs.error:
                raw_files = obs.raw_data if isinstance(obs.raw_data, list) else obs.raw_data.get("files", [])
                for f in raw_files:
                    files.append(FileContext(
                        path=f.get("path", ""),
                        content=f.get("content", ""),
                        start_line=f.get("start_line", 1),
                        end_line=f.get("end_line", 0),
                        is_full_file=f.get("is_full_file", True),
                    ))
            elif obs.observation_type == ObservationType.GREP_RESULT and not obs.error:
                matches = obs.raw_data if isinstance(obs.raw_data, list) else obs.raw_data.get("grep_matches", [])
                grep_matches.extend(matches)

        return GatheredContext(
            files=files,
            grep_matches=grep_matches,
            dependency_chain=list(belief.owner_candidates.keys()),
        )

    def _finish(
        self,
        status: str,
        ticket: TicketInput,
        belief: BeliefState,
        budget: TicketBudget,
        report: str = "",
        reason: str = "",
        error: str = "",
        questions: list[str] | None = None,
        reasoner_output: ReasonerOutput | None = None,
    ) -> dict[str, Any]:
        """Finalize the run — save experience and return results."""
        elapsed = time.time() - budget.start_time

        # Save experience for future tickets (real ticket type, not 'unknown')
        ticket_type = self._infer_ticket_type(ticket)
        experience = self.experience_store.create_experience_from_belief(
            ticket_id=ticket.ticket_id,
            ticket_type=ticket_type,
            belief=belief,
            outcome=status,
            total_tokens=budget.tokens_used,
            total_time=elapsed,
            gate_logs=[g.to_log_entry() for g in self._gate_history],
        )
        self.experience_store.save(experience)

        logger.info(
            "═══ FINISHED ticket=%s status=%s steps=%d tokens=%d time=%.1fs ═══",
            ticket.ticket_id, status, belief.step_count,
            budget.tokens_used, elapsed,
        )

        result: dict[str, Any] = {
            "status": status,
            "belief": belief.to_context_string(),
            "budget": budget.summary(),
            "gate_logs": [g.to_log_entry() for g in self._gate_history],
            "steps_taken": belief.step_count,
            "theories": [
                {"id": t.id, "hypothesis": t.hypothesis, "status": t.status.value, "confidence": t.confidence}
                for t in belief.theories
            ],
        }

        if report:
            result["report"] = report
        if reason:
            result["reason"] = reason
        if error:
            result["error"] = error
        if questions:
            result["questions"] = questions
        if reasoner_output:
            result["patches"] = [
                {"file": p.file_path, "hunks": len(p.hunks)}
                for p in reasoner_output.patches
            ]

        return result


# ---------------------------------------------------------------------------
# Factory — creates the adaptive orchestrator with LLMRegistry
# ---------------------------------------------------------------------------

def create_adaptive_orchestrator(
    gate_mode: GateMode = GateMode.ADVISORY,
) -> AdaptiveOrchestrator:
    """Create an AdaptiveOrchestrator using the existing LLMRegistry.

    Model mapping:
        planner_llm  → LLMRegistry.get_llm(assistant=False) → gemini-2.5-flash-lite
        reasoner_llm → LLMRegistry.get_llm(assistant=True)  → gemini-2.5-flash
        reporter_llm → LLMRegistry.get_llm(assistant=False) → gemini-2.5-flash-lite
    """
    from aviator.services.llm import LLMRegistry

    return AdaptiveOrchestrator(
        planner_llm=LLMRegistry.get_llm(assistant=False),
        reasoner_llm=LLMRegistry.get_llm(assistant=True),
        reporter_llm=LLMRegistry.get_llm(assistant=False),
        gate_mode=gate_mode,
    )
