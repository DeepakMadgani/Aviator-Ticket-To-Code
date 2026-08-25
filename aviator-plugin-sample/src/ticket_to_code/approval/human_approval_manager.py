"""
Human Approval Manager - User Control Layer

Handles confidence-based approvals and manual file selection.

Confidence Thresholds:
- 0.90-1.00: Very high (auto-proceed, optional review)
- 0.75-0.89: High (auto-proceed with notification)
- 0.50-0.74: Medium (require approval)
- 0.00-0.49: Low (require approval + manual selection)

Author: Deepak Madgani
Date: May 27, 2026
"""

import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

from ticket_to_code.models import (
    LocalizationResult,
    TargetFile,
    TargetMethod,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalMode
)

logger = logging.getLogger(__name__)


class HumanApprovalManager:
    """
    Manages human-in-the-loop approvals based on confidence scores.
    
    Provides:
    - Confidence-based approval triggers
    - Manual file selection/addition
    - Patch preview
    - Impact analysis review
    - User can override any decision
    """
    
    def __init__(self, mode: ApprovalMode = ApprovalMode.CONFIDENCE_BASED):
        """
        Initialize approval manager.
        
        Args:
            mode: Approval mode
                - ALWAYS: Always ask for approval
                - CONFIDENCE_BASED: Ask based on confidence threshold
                - NEVER: Never ask (fully autonomous)
        """
        self.mode = mode
        self.confidence_thresholds = {
            "very_high": 0.90,  # Auto-proceed
            "high": 0.75,       # Auto-proceed with notification
            "medium": 0.50,     # Require approval
            "low": 0.00         # Require approval + manual selection
        }
        
        logger.info(f"Human Approval Manager initialized (mode: {mode})")
    
    def should_request_approval(
        self,
        localization_result: LocalizationResult,
        phase: str = "localization"
    ) -> bool:
        """
        Determine if human approval is needed.
        
        Args:
            localization_result: Localization result with confidence
            phase: Which phase (localization, planning, generation)
            
        Returns:
            True if approval needed, False if auto-proceed
        """
        confidence = localization_result.confidence
        
        # Mode 1: ALWAYS ask
        if self.mode == ApprovalMode.ALWAYS:
            logger.info(f"Mode=ALWAYS → Approval required")
            return True
        
        # Mode 2: NEVER ask (fully autonomous)
        if self.mode == ApprovalMode.NEVER:
            logger.info(f"Mode=NEVER → Auto-proceeding")
            return False
        
        # Mode 3: CONFIDENCE_BASED (default)
        if confidence >= self.confidence_thresholds["high"]:
            logger.info(f"Confidence {confidence:.2f} ≥ 0.75 → Auto-proceeding")
            return False
        else:
            logger.info(f"Confidence {confidence:.2f} < 0.75 → Approval required")
            return True
    
    def create_approval_request(
        self,
        localization_result: LocalizationResult,
        available_files: Optional[List[str]] = None
    ) -> ApprovalRequest:
        """
        Create approval request for UI.
        
        Args:
            localization_result: Localization result
            available_files: All available files in project (for manual selection)
            
        Returns:
            ApprovalRequest object for UI
        """
        confidence = localization_result.confidence
        
        # Determine approval level
        if confidence >= self.confidence_thresholds["very_high"]:
            approval_level = "optional"
            message = "Very high confidence - auto-proceeding"
        elif confidence >= self.confidence_thresholds["high"]:
            approval_level = "notification"
            message = "High confidence - proceeding with notification"
        elif confidence >= self.confidence_thresholds["medium"]:
            approval_level = "required"
            message = "Medium confidence - please review"
        else:
            approval_level = "manual_selection"
            message = "Low confidence - please review and manually select files"
        
        # Create request
        request = ApprovalRequest(
            phase="localization",
            confidence=confidence,
            approval_level=approval_level,
            message=message,
            
            # What AI found
            suggested_files=localization_result.target_files,
            suggested_methods=localization_result.target_methods,
            dependencies=localization_result.dependencies,
            execution_paths=localization_result.execution_paths,
            impact_analysis=localization_result.impact_analysis,
            
            # Allow manual selection
            available_files=available_files or [],
            allow_manual_addition=True,
            allow_file_removal=True,
            
            # Timestamps
            created_at=datetime.now()
        )
        
        logger.info(
            f"Created approval request:\n"
            f"  Level: {approval_level}\n"
            f"  Confidence: {confidence:.2f}\n"
            f"  Suggested files: {len(request.suggested_files)}\n"
            f"  Manual selection: {request.allow_manual_addition}"
        )
        
        return request
    
    def process_approval_response(
        self,
        response: ApprovalResponse
    ) -> LocalizationResult:
        """
        Process user's approval response.
        
        User can:
        - Approve suggested files
        - Add additional files
        - Remove suggested files
        - Modify task types (CREATE vs MODIFY)
        
        Args:
            response: User's approval response
            
        Returns:
            Updated LocalizationResult
        """
        logger.info(f"Processing approval response: {response.action}")
        
        if response.action == "cancel":
            logger.info("User cancelled workflow")
            raise WorkflowCancelledException("User cancelled")
        
        if response.action == "request_changes":
            logger.info(f"User requested changes: {response.feedback}")
            raise WorkflowModificationRequested(response.feedback)
        
        # User approved (with or without modifications)
        final_files = []
        
        # Add approved suggested files
        for file in response.approved_files:
            final_files.append(file)
        
        # Add user-added files
        for file_path in response.additional_files:
            # Create TargetFile from user input
            target_file = TargetFile(
                file_path=file_path,
                absolute_path=str(Path(response.workspace_path) / file_path),
                language=self._detect_language(file_path),
                task_type=response.file_task_types.get(file_path, "modify"),
                confidence=1.0,  # User specified = 100% confidence
                reason="Manually added by user"
            )
            final_files.append(target_file)
        
        # Create updated LocalizationResult
        updated_result = LocalizationResult(
            target_files=final_files,
            target_methods=response.approved_methods or [],
            dependencies=response.original_result.dependencies,
            execution_paths=response.original_result.execution_paths,
            impact_analysis=response.original_result.impact_analysis,
            confidence=1.0,  # User approved = 100% confidence
            requires_human_review=False,
            user_approved=True,
            user_feedback=response.feedback
        )
        
        logger.info(
            f"Approval processed:\n"
            f"  Final files: {len(updated_result.target_files)}\n"
            f"  User added: {len(response.additional_files)}\n"
            f"  Confidence: 1.0 (user approved)"
        )
        
        return updated_result
    
    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension"""
        extension_map = {
            ".java": "java",
            ".cs": "csharp",
            ".ts": "typescript",
            ".js": "javascript",
            ".py": "python",
            ".go": "go",
            ".xml": "xml",
            ".json": "json"
        }
        
        ext = Path(file_path).suffix
        return extension_map.get(ext, "unknown")
    
    def get_all_project_files(
        self,
        workspace_path: str,
        extensions: List[str] = [".java", ".cs", ".ts", ".py"]
    ) -> List[str]:
        """
        Get all files in project for manual selection.
        
        Args:
            workspace_path: Project root path
            extensions: File extensions to include
            
        Returns:
            List of relative file paths
        """
        workspace = Path(workspace_path)
        all_files = []
        
        for ext in extensions:
            for file_path in workspace.rglob(f"*{ext}"):
                # Skip generated, test, and build directories
                if any(skip in str(file_path) for skip in ["target", "build", "node_modules", ".git"]):
                    continue
                
                relative_path = file_path.relative_to(workspace)
                all_files.append(str(relative_path))
        
        logger.info(f"Found {len(all_files)} project files for selection")
        return sorted(all_files)


class WorkflowCancelledException(Exception):
    """Raised when user cancels workflow"""
    pass


class WorkflowModificationRequested(Exception):
    """Raised when user requests workflow modifications"""
    pass


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def request_approval(
    localization_result: LocalizationResult,
    workspace_path: str,
    mode: ApprovalMode = ApprovalMode.CONFIDENCE_BASED
) -> LocalizationResult:
    """
    Request approval from user if needed.
    
    Args:
        localization_result: Localization result
        workspace_path: Project workspace path
        mode: Approval mode
        
    Returns:
        Approved (possibly modified) LocalizationResult
    """
    manager = HumanApprovalManager(mode=mode)
    
    # Check if approval needed
    if not manager.should_request_approval(localization_result):
        logger.info("Auto-proceeding without approval")
        return localization_result
    
    # Get all project files for manual selection
    available_files = manager.get_all_project_files(workspace_path)
    
    # Create approval request
    approval_request = manager.create_approval_request(
        localization_result,
        available_files=available_files
    )
    
    # Send to UI (this would be handled by backend API)
    logger.info(f"Sending approval request to UI...")
    
    # Wait for response (in real system, this is async via WebSocket)
    # For now, we'll assume auto-approval for high confidence
    if localization_result.confidence >= 0.75:
        logger.info("High confidence - auto-approving")
        return localization_result
    else:
        logger.warning("Low confidence - requires user approval")
        # In production, this would wait for user response via API
        return localization_result
