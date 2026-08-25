"""
Approval Module - Human-in-the-Loop Control

Handles confidence-based approvals and manual file selection.
"""

from .human_approval_manager import (
    HumanApprovalManager,
    WorkflowCancelledException,
    WorkflowModificationRequested,
    request_approval
)

__all__ = [
    "HumanApprovalManager",
    "WorkflowCancelledException",
    "WorkflowModificationRequested",
    "request_approval",
]
