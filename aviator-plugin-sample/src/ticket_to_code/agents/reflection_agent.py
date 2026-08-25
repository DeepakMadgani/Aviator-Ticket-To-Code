"""
Phase 5: Reflection Agent - Quality Gate Before Generation

Questions assumptions and checks evidence gaps before committing to code generation.
Implements the "thoughtful pause" before generation to catch issues early.

Author: Deepak Madgani
Date: April 2026
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ReflectionAgent:
    """
    Quality gate agent that reflects on plan before generation.
    
    This agent implements the critical thinking step:
    1. Question all assumptions
    2. Identify evidence gaps
    3. Consider alternatives
    4. Make final confidence judgment
    5. Decide whether to proceed to generation
    """
    
    def __init__(self):
        """Initialize reflection agent"""
        self.logger = logging.getLogger(__name__)
    
    def reflect(
        self,
        investigation_result: Dict[str, Any],
        architectural_plan: Dict[str, Any],
        requirements: Dict[str, Any],
        discovery_results: Optional[Dict[str, Any]] = None,
        localization_results: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Reflect on the architectural plan and discovery results.
        
        Args:
            investigation_result: Output from InvestigationAgent
            architectural_plan: Output from PlanningAgent
            requirements: Structured requirements from ticket
            discovery_results: Optional discovery phase results
            localization_results: Optional localization phase results
        
        Returns:
            {
                'assumptions_validated': bool,
                'assumptions': List[str],
                'evidence_gaps': List[str],
                'alternative_approaches': List[str],
                'misunderstandings': List[str],
                'concerns': List[str],
                'updated_planning_confidence': float,
                'proceed_to_generation': bool,
                'reflection_reason': str,
                'timestamp': datetime
            }
        """
        
        self.logger.info(f"[REFLECTION] Starting reflection on plan")
        
        # Extract data from inputs
        root_cause = investigation_result.get('root_cause', '')
        fix_strategy = architectural_plan.get('strategy', '') or architectural_plan.get('fix_strategy', '')
        files_to_modify = architectural_plan.get('files_to_modify', [])
        methods_to_modify = architectural_plan.get('methods_to_modify', [])
        requirements_list = requirements.get('requirements', [])
        acceptance_criteria = requirements.get('acceptance_criteria', [])
        
        # 1. IDENTIFY ASSUMPTIONS
        assumptions = self._identify_assumptions(
            root_cause,
            fix_strategy,
            files_to_modify,
            requirements_list
        )
        self.logger.debug(f"Identified {len(assumptions)} assumptions")
        
        # 2. VALIDATE ASSUMPTIONS
        assumptions_validated = self._validate_assumptions(
            assumptions,
            discovery_results,
            localization_results
        )
        self.logger.debug(f"Assumptions validated: {assumptions_validated}")
        
        # 3. IDENTIFY EVIDENCE GAPS
        evidence_gaps = self._identify_evidence_gaps(
            root_cause,
            fix_strategy,
            discovery_results,
            localization_results
        )
        self.logger.debug(f"Found {len(evidence_gaps)} evidence gaps")
        
        # 4. CONSIDER ALTERNATIVES
        alternatives = self._consider_alternatives(
            root_cause,
            files_to_modify,
            requirements_list
        )
        self.logger.debug(f"Identified {len(alternatives)} alternative approaches")
        
        # 5. CHECK FOR MISUNDERSTANDINGS
        misunderstandings = self._check_misunderstandings(
            root_cause,
            acceptance_criteria,
            methods_to_modify
        )
        self.logger.debug(f"Found {len(misunderstandings)} potential misunderstandings")
        
        # 6. IDENTIFY CONCERNS
        concerns = self._identify_concerns(
            files_to_modify,
            methods_to_modify,
            fix_strategy,
            discovery_results
        )
        self.logger.debug(f"Identified {len(concerns)} concerns")
        
        # 7. UPDATE CONFIDENCE
        original_confidence = architectural_plan.get('confidence', 0.7)
        updated_confidence = self._update_confidence(
            original_confidence,
            assumptions_validated,
            evidence_gaps,
            concerns,
            misunderstandings
        )
        self.logger.debug(f"Confidence: {original_confidence:.2f} → {updated_confidence:.2f}")
        
        # 8. DECIDE WHETHER TO PROCEED
        proceed = updated_confidence > 0.65  # Threshold for proceeding to generation
        reason = "Reflection passed - confidence sufficient" if proceed else "Reflection failed - confidence too low"
        
        if not proceed:
            self.logger.warning(f"Reflection gate blocked generation: {reason}")
        
        result = {
            'assumptions_validated': assumptions_validated,
            'assumptions': assumptions,
            'evidence_gaps': evidence_gaps,
            'alternative_approaches': alternatives,
            'misunderstandings': misunderstandings,
            'concerns': concerns,
            'updated_planning_confidence': updated_confidence,
            'proceed_to_generation': proceed,
            'reflection_reason': reason,
            'timestamp': datetime.now()
        }
        
        self.logger.info(f"[REFLECTION] Result: proceed={proceed}, confidence={updated_confidence:.2f}")
        
        return result
    
    def _identify_assumptions(
        self,
        root_cause: str,
        fix_strategy: str,
        files_to_modify: List[str],
        requirements: List[str]
    ) -> List[str]:
        """
        Identify all assumptions being made about the fix.
        
        TODO: Call LLM to extract assumptions in natural language
        For now, return heuristic list.
        """
        
        assumptions = []
        
        # Assumption 1: Root cause is the real cause
        if root_cause:
            assumptions.append(f"Root cause '{root_cause}' is the actual problem")
        
        # Assumption 2: Only these files need modification
        if files_to_modify:
            assumptions.append(f"Only {len(files_to_modify)} files need modification")
            assumptions.append(f"No other files have related bugs")
        
        # Assumption 3: Fix strategy will work
        if fix_strategy:
            assumptions.append(f"Strategy '{fix_strategy}' will solve the issue")
            assumptions.append(f"No unintended side effects from this strategy")
        
        # Assumption 4: Requirements are complete
        if requirements:
            assumptions.append(f"All {len(requirements)} requirements are valid")
            assumptions.append(f"No hidden requirements in the codebase")
        
        return assumptions
    
    def _validate_assumptions(
        self,
        assumptions: List[str],
        discovery_results: Optional[Dict],
        localization_results: Optional[Dict]
    ) -> bool:
        """
        Check if assumptions are supported by evidence.
        
        TODO: Call LLM to validate assumptions against evidence
        For now, simple heuristic: if we have discovery and localization results, assumptions are likely valid
        """
        
        if not assumptions:
            return True
        
        # Check if we have supporting evidence
        has_discovery_evidence = (
            discovery_results and 
            len(discovery_results.get('results', [])) > 0
        )
        
        has_localization_evidence = (
            localization_results and 
            len(localization_results.get('files', [])) > 0
        )
        
        # If we have both discovery and localization support, assumptions are validated
        return has_discovery_evidence and has_localization_evidence
    
    def _identify_evidence_gaps(
        self,
        root_cause: str,
        fix_strategy: str,
        discovery_results: Optional[Dict],
        localization_results: Optional[Dict]
    ) -> List[str]:
        """
        Identify gaps in our evidence chain.
        
        TODO: Call LLM to identify gaps
        For now, return heuristic gaps
        """
        
        gaps = []
        
        # Gap 1: Root cause not confirmed by multiple sources
        if root_cause and discovery_results:
            num_results = len(discovery_results.get('results', []))
            if num_results < 3:
                gaps.append(f"Root cause only confirmed by {num_results} sources (need 3+)")
        
        # Gap 2: Missing dependency analysis
        if discovery_results:
            dependencies = discovery_results.get('dependencies', [])
            if not dependencies:
                gaps.append("No dependency analysis performed")
        
        # Gap 3: No test coverage analysis
        if discovery_results:
            tests = discovery_results.get('affected_tests', [])
            if not tests:
                gaps.append("No affected tests identified")
        
        # Gap 4: No performance impact analysis
        if fix_strategy and 'performance' not in fix_strategy.lower():
            gaps.append("No performance impact analysis documented")
        
        return gaps
    
    def _consider_alternatives(
        self,
        root_cause: str,
        files_to_modify: List[str],
        requirements: List[str]
    ) -> List[str]:
        """
        Consider alternative approaches to solving the problem.
        
        TODO: Call LLM to generate alternatives
        For now, return generic alternatives
        """
        
        alternatives = []
        
        if len(files_to_modify) > 3:
            alternatives.append("Consider refactoring to reduce file changes")
        
        if requirements:
            alternatives.append("Could implement requirement differently in a different layer")
            alternatives.append("Could solve at architectural level instead of implementation level")
        
        alternatives.append("Could add configuration instead of code change")
        alternatives.append("Could solve at deployment level instead of code level")
        
        return alternatives
    
    def _check_misunderstandings(
        self,
        root_cause: str,
        acceptance_criteria: List[str],
        methods_to_modify: List[str]
    ) -> List[str]:
        """
        Check if we might have misunderstood the ticket.
        
        TODO: Call LLM to check for misunderstandings
        For now, return heuristic checks
        """
        
        misunderstandings = []
        
        # Misunderstanding 1: Did we interpret acceptance criteria correctly?
        if acceptance_criteria:
            if not methods_to_modify:
                misunderstandings.append("Acceptance criteria require method changes but none identified")
        
        # Misunderstanding 2: Root cause seems too simple
        if root_cause and len(root_cause) < 20:
            misunderstandings.append("Root cause description is very brief - might be oversimplified")
        
        # Misunderstanding 3: No methods to modify but changes needed
        if not methods_to_modify and acceptance_criteria:
            misunderstandings.append("No methods to modify but acceptance criteria suggest code changes needed")
        
        return misunderstandings
    
    def _identify_concerns(
        self,
        files_to_modify: List[str],
        methods_to_modify: List[str],
        fix_strategy: str,
        discovery_results: Optional[Dict]
    ) -> List[str]:
        """
        Identify concerns about the proposed fix.
        
        TODO: Call LLM to identify concerns
        For now, return heuristic concerns
        """
        
        concerns = []
        
        # Concern 1: Large number of files
        if len(files_to_modify) > 5:
            concerns.append(f"Large number of files to modify ({len(files_to_modify)}) - risk of unintended effects")
        
        # Concern 2: Breaking changes
        if 'remove' in fix_strategy.lower() or 'delete' in fix_strategy.lower():
            concerns.append("Fix strategy involves removing code - ensure no breaking changes")
        
        # Concern 3: No circular dependency checks
        if discovery_results and not discovery_results.get('dependency_analysis'):
            concerns.append("No circular dependency analysis - check if changes introduce cycles")
        
        # Concern 4: Complex fix strategy
        if fix_strategy and len(fix_strategy) > 200:
            concerns.append("Fix strategy is complex - ensure it's necessary")
        
        # Concern 5: Too many methods
        if len(methods_to_modify) > 10:
            concerns.append(f"Large number of methods to modify ({len(methods_to_modify)}) - consider decomposing")
        
        return concerns
    
    def _update_confidence(
        self,
        original_confidence: float,
        assumptions_validated: bool,
        evidence_gaps: List[str],
        concerns: List[str],
        misunderstandings: List[str]
    ) -> float:
        """
        Update confidence based on reflection findings.
        
        Confidence decreases for each gap, concern, or misunderstanding.
        """
        
        confidence = original_confidence
        
        # Penalty for unvalidated assumptions
        if not assumptions_validated:
            confidence -= 0.15
        
        # Penalty for each evidence gap
        confidence -= len(evidence_gaps) * 0.05
        
        # Penalty for each concern
        confidence -= len(concerns) * 0.03
        
        # Penalty for each misunderstanding
        confidence -= len(misunderstandings) * 0.08
        
        # Ensure confidence stays in valid range
        return max(0.0, min(1.0, confidence))
