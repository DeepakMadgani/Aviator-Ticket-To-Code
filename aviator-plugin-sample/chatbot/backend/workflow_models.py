"""
Workflow state models for transparent execution tracking.
"""
from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime


class WorkflowPhase(str, Enum):
    IDLE = "idle"
    INDEXING = "indexing"
    CLASSIFICATION = "classification"
    LOCALIZATION = "localization"
    IMPACT_ANALYSIS = "impact_analysis"
    HUMAN_REVIEW_FILES = "human_review_files"
    CONTEXT_LOADING = "context_loading"
    PATCH_GENERATION = "patch_generation"
    VALIDATION = "validation"
    HUMAN_FINAL_REVIEW = "human_final_review"
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"


class OperationType(str, Enum):
    CODE_MODIFICATION = "code_modification"
    CODE_ADDITION = "code_addition"
    REFACTORING = "refactoring"
    BUG_FIX = "bug_fix"


class FileCandidate(BaseModel):
    path: str
    score: float
    reason: str
    method_name: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    selected: bool = True  # User can toggle


class ImpactAnalysis(BaseModel):
    direct_callers: int = 0
    transitive_dependents: int = 0
    affected_tests: int = 0
    risk_level: str = "low"  # low, medium, high, critical
    breaks_api: bool = False


class WorkflowStep(BaseModel):
    phase: WorkflowPhase
    status: str  # running, completed, waiting_approval, failed
    message: str
    timestamp: datetime = Field(default_factory=datetime.now)
    data: Optional[dict[str, Any]] = None


class WorkflowState(BaseModel):
    workflow_id: str
    ticket_id: str
    ticket_description: str
    current_phase: WorkflowPhase = WorkflowPhase.IDLE
    operation_type: Optional[OperationType] = None
    
    # Localization results
    candidate_files: list[FileCandidate] = []
    selected_files: list[str] = []
    impact_analysis: Optional[ImpactAnalysis] = None
    
    # User confirmations
    files_approved: bool = False
    operation_approved: bool = False
    final_approved: bool = False
    
    # Progress tracking
    steps: list[WorkflowStep] = []
    current_step_index: int = 0
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
