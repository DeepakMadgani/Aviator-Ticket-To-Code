"""
Runtime utilities: RunContext, RunBudget, DegradationRegistry.
"""

from ticket_to_code.runtime.run_context import (
    HealthLevel,
    RunBudget,
    BudgetExceeded,
    DegradationRegistry,
    RunContext,
    RunRecord,
)
from ticket_to_code.runtime.intake_contract import (
    TicketKind,
    Priority,
    RiskLevel,
    RunStatus,
    ValidationResult,
    GroundTruthTicket,
    SuccessPolicy,
    ConstraintPolicy,
    StageRetryPolicy,
    DecisionPriority,
    DecisionRecord,
    ChangedFileRecord,
    ValidationEvidenceRecord,
    TicketRunReport,
    OrchestratorPolicy,
    enforce_hard_gates,
    summarize_validation,
    report_to_dict,
)
from ticket_to_code.runtime.orchestrator_skeleton import (
    StageFns,
    ContractOrchestrator,
)
from ticket_to_code.runtime.stage_depth import (
    StageDepthInput,
    StageDepthDecision,
    choose_stage_depth,
    apply_gate_overrides,
)
from ticket_to_code.runtime.tool_adapter_contract import (
    ToolAdapter,
)

__all__ = [
    "HealthLevel",
    "RunBudget",
    "BudgetExceeded",
    "DegradationRegistry",
    "RunContext",
    "RunRecord",
    "TicketKind",
    "Priority",
    "RiskLevel",
    "RunStatus",
    "ValidationResult",
    "GroundTruthTicket",
    "SuccessPolicy",
    "ConstraintPolicy",
    "StageRetryPolicy",
    "DecisionPriority",
    "DecisionRecord",
    "ChangedFileRecord",
    "ValidationEvidenceRecord",
    "TicketRunReport",
    "OrchestratorPolicy",
    "enforce_hard_gates",
    "summarize_validation",
    "report_to_dict",
    "StageFns",
    "ContractOrchestrator",
    "StageDepthInput",
    "StageDepthDecision",
    "choose_stage_depth",
    "apply_gate_overrides",
    "ToolAdapter",
]
