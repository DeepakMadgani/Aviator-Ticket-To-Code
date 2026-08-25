"""
Static Analysis Gates - Code Quality Validation

AI-generated code MUST pass static analysis before merge.

Tests passing is NOT enough - need:
- Checkstyle (formatting, conventions)
- PMD (code quality, best practices)
- SpotBugs (bug detection)
- SonarQube (comprehensive analysis)

Author: Deepak Madgani
Date: May 27, 2026
"""

import logging
import subprocess
from typing import List, Optional, Dict, Any
from pathlib import Path
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AnalysisTool(str, Enum):
    """Static analysis tools"""
    CHECKSTYLE = "checkstyle"
    PMD = "pmd"
    SPOTBUGS = "spotbugs"
    SONARQUBE = "sonarqube"
    ESLINT = "eslint"  # For TypeScript/JavaScript
    PYLINT = "pylint"  # For Python


class Severity(str, Enum):
    """Issue severity levels"""
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"
    BLOCKER = "blocker"


class AnalysisIssue(BaseModel):
    """Single static analysis issue"""
    tool: AnalysisTool = Field(..., description="Tool that found this issue")
    file_path: str = Field(..., description="File with issue")
    line: int = Field(..., description="Line number")
    column: Optional[int] = Field(None, description="Column number")
    severity: Severity = Field(..., description="Issue severity")
    rule_id: str = Field(..., description="Rule ID (e.g., PMD.UnusedVariable)")
    message: str = Field(..., description="Issue description")
    suggestion: Optional[str] = Field(None, description="How to fix")


class AnalysisResult(BaseModel):
    """Result of static analysis"""
    tool: AnalysisTool = Field(..., description="Analysis tool")
    success: bool = Field(..., description="Whether analysis passed")
    issues: List[AnalysisIssue] = Field(default_factory=list, description="Found issues")
    files_analyzed: int = Field(default=0, description="Number of files analyzed")
    duration_seconds: float = Field(default=0.0, description="Analysis duration")
    summary: Dict[str, int] = Field(
        default_factory=dict,
        description="Summary by severity {critical: 2, major: 5, ...}"
    )


class QualityGateResult(BaseModel):
    """Result of all quality gates"""
    passed: bool = Field(..., description="Whether all gates passed")
    results: List[AnalysisResult] = Field(..., description="Individual tool results")
    total_issues: int = Field(default=0, description="Total issues found")
    blocker_issues: int = Field(default=0, description="Blocker issues")
    critical_issues: int = Field(default=0, description="Critical issues")
    major_issues: int = Field(default=0, description="Major issues")
    can_merge: bool = Field(..., description="Whether code can be merged")
    blocking_reasons: List[str] = Field(default_factory=list, description="Why merge blocked")


class StaticAnalysisGate:
    """
    Static analysis quality gates.
    
    Runs multiple analysis tools and enforces quality standards.
    AI-generated code must pass ALL gates before merge.
    """
    
    def __init__(
        self,
        workspace_path: str,
        enabled_tools: Optional[List[AnalysisTool]] = None
    ):
        """
        Initialize static analysis gates.
        
        Args:
            workspace_path: Project root
            enabled_tools: Which tools to run (None = all available)
        """
        self.workspace_path = Path(workspace_path)
        self.enabled_tools = enabled_tools or [
            AnalysisTool.CHECKSTYLE,
            AnalysisTool.PMD,
            AnalysisTool.SPOTBUGS
        ]
        
        logger.info(f"Static Analysis Gates initialized with {len(self.enabled_tools)} tools")
    
    def run_quality_gates(
        self,
        files: Optional[List[str]] = None
    ) -> QualityGateResult:
        """
        Run all quality gates on specified files.
        
        Args:
            files: Files to analyze (None = all files)
            
        Returns:
            QualityGateResult with pass/fail status
        """
        logger.info("🔍 Running static analysis quality gates...")
        
        results = []
        
        # Run each enabled tool
        for tool in self.enabled_tools:
            logger.info(f"Running {tool}...")
            
            if tool == AnalysisTool.CHECKSTYLE:
                result = self._run_checkstyle(files)
            elif tool == AnalysisTool.PMD:
                result = self._run_pmd(files)
            elif tool == AnalysisTool.SPOTBUGS:
                result = self._run_spotbugs(files)
            elif tool == AnalysisTool.SONARQUBE:
                result = self._run_sonarqube(files)
            else:
                logger.warning(f"Tool not implemented: {tool}")
                continue
            
            results.append(result)
            
            logger.info(
                f"{tool} complete: "
                f"{len(result.issues)} issues found, "
                f"passed: {result.success}"
            )
        
        # Aggregate results
        return self._aggregate_results(results)
    
    def _run_checkstyle(
        self,
        files: Optional[List[str]] = None
    ) -> AnalysisResult:
        """
        Run Checkstyle - code style checker.
        
        Checks:
        - Formatting
        - Naming conventions
        - Javadoc
        - Import order
        - Whitespace
        """
        from datetime import datetime
        start_time = datetime.now()
        
        try:
            # Maven Checkstyle plugin
            cmd = ["mvn", "checkstyle:check"]
            
            result = subprocess.run(
                cmd,
                cwd=self.workspace_path,
                capture_output=True,
                text=True
            )
            
            # Parse Checkstyle XML output
            issues = self._parse_checkstyle_output(result.stdout)
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # Determine success (no major violations)
            major_issues = sum(
                1 for i in issues 
                if i.severity in [Severity.MAJOR, Severity.CRITICAL, Severity.BLOCKER]
            )
            
            return AnalysisResult(
                tool=AnalysisTool.CHECKSTYLE,
                success=major_issues == 0,
                issues=issues,
                files_analyzed=len(files) if files else 0,
                duration_seconds=duration,
                summary=self._summarize_issues(issues)
            )
            
        except Exception as e:
            logger.error(f"Checkstyle failed: {e}")
            return AnalysisResult(
                tool=AnalysisTool.CHECKSTYLE,
                success=False,
                issues=[],
                duration_seconds=0.0
            )
    
    def _run_pmd(
        self,
        files: Optional[List[str]] = None
    ) -> AnalysisResult:
        """
        Run PMD - static code analyzer.
        
        Checks:
        - Unused variables
        - Empty catch blocks
        - Unnecessary object creation
        - Complex code
        - Potential bugs
        """
        from datetime import datetime
        start_time = datetime.now()
        
        try:
            # Maven PMD plugin
            cmd = ["mvn", "pmd:check"]
            
            result = subprocess.run(
                cmd,
                cwd=self.workspace_path,
                capture_output=True,
                text=True
            )
            
            # Parse PMD output
            issues = self._parse_pmd_output(result.stdout)
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # PMD returns non-zero if violations found
            return AnalysisResult(
                tool=AnalysisTool.PMD,
                success=result.returncode == 0,
                issues=issues,
                files_analyzed=len(files) if files else 0,
                duration_seconds=duration,
                summary=self._summarize_issues(issues)
            )
            
        except Exception as e:
            logger.error(f"PMD failed: {e}")
            return AnalysisResult(
                tool=AnalysisTool.PMD,
                success=False,
                issues=[],
                duration_seconds=0.0
            )
    
    def _run_spotbugs(
        self,
        files: Optional[List[str]] = None
    ) -> AnalysisResult:
        """
        Run SpotBugs - bug detection tool.
        
        Checks:
        - Null pointer dereference
        - Resource leaks
        - Concurrency issues
        - Security vulnerabilities
        - Performance issues
        """
        from datetime import datetime
        start_time = datetime.now()
        
        try:
            # Maven SpotBugs plugin
            cmd = ["mvn", "spotbugs:check"]
            
            result = subprocess.run(
                cmd,
                cwd=self.workspace_path,
                capture_output=True,
                text=True
            )
            
            # Parse SpotBugs output
            issues = self._parse_spotbugs_output(result.stdout)
            
            duration = (datetime.now() - start_time).total_seconds()
            
            return AnalysisResult(
                tool=AnalysisTool.SPOTBUGS,
                success=result.returncode == 0,
                issues=issues,
                files_analyzed=len(files) if files else 0,
                duration_seconds=duration,
                summary=self._summarize_issues(issues)
            )
            
        except Exception as e:
            logger.error(f"SpotBugs failed: {e}")
            return AnalysisResult(
                tool=AnalysisTool.SPOTBUGS,
                success=False,
                issues=[],
                duration_seconds=0.0
            )
    
    def _run_sonarqube(
        self,
        files: Optional[List[str]] = None
    ) -> AnalysisResult:
        """
        Run SonarQube - comprehensive code quality platform.
        
        Checks:
        - Code smells
        - Bugs
        - Vulnerabilities
        - Security hotspots
        - Code coverage
        - Duplication
        """
        from datetime import datetime
        start_time = datetime.now()
        
        try:
            # Maven SonarQube plugin
            cmd = [
                "mvn",
                "sonar:sonar",
                "-Dsonar.host.url=http://localhost:9000"
            ]
            
            result = subprocess.run(
                cmd,
                cwd=self.workspace_path,
                capture_output=True,
                text=True
            )
            
            # Parse SonarQube output
            issues = self._parse_sonarqube_output(result.stdout)
            
            duration = (datetime.now() - start_time).total_seconds()
            
            return AnalysisResult(
                tool=AnalysisTool.SONARQUBE,
                success=result.returncode == 0,
                issues=issues,
                files_analyzed=len(files) if files else 0,
                duration_seconds=duration,
                summary=self._summarize_issues(issues)
            )
            
        except Exception as e:
            logger.error(f"SonarQube failed: {e}")
            return AnalysisResult(
                tool=AnalysisTool.SONARQUBE,
                success=False,
                issues=[],
                duration_seconds=0.0
            )
    
    def _parse_checkstyle_output(self, output: str) -> List[AnalysisIssue]:
        """Parse Checkstyle output to issues"""
        issues = []
        # TODO: Parse XML output
        # For now, return empty list
        return issues
    
    def _parse_pmd_output(self, output: str) -> List[AnalysisIssue]:
        """Parse PMD output to issues"""
        issues = []
        # TODO: Parse XML output
        return issues
    
    def _parse_spotbugs_output(self, output: str) -> List[AnalysisIssue]:
        """Parse SpotBugs output to issues"""
        issues = []
        # TODO: Parse XML output
        return issues
    
    def _parse_sonarqube_output(self, output: str) -> List[AnalysisIssue]:
        """Parse SonarQube output to issues"""
        issues = []
        # TODO: Parse JSON API response
        return issues
    
    def _summarize_issues(self, issues: List[AnalysisIssue]) -> Dict[str, int]:
        """Create summary of issues by severity"""
        summary = {
            "blocker": 0,
            "critical": 0,
            "major": 0,
            "minor": 0,
            "info": 0
        }
        
        for issue in issues:
            summary[issue.severity] += 1
        
        return summary
    
    def _aggregate_results(
        self,
        results: List[AnalysisResult]
    ) -> QualityGateResult:
        """Aggregate results from all tools"""
        
        all_issues = []
        for result in results:
            all_issues.extend(result.issues)
        
        # Count by severity
        blocker_issues = sum(1 for i in all_issues if i.severity == Severity.BLOCKER)
        critical_issues = sum(1 for i in all_issues if i.severity == Severity.CRITICAL)
        major_issues = sum(1 for i in all_issues if i.severity == Severity.MAJOR)
        
        # Determine if can merge
        blocking_reasons = []
        
        if blocker_issues > 0:
            blocking_reasons.append(f"{blocker_issues} blocker issues")
        
        if critical_issues > 0:
            blocking_reasons.append(f"{critical_issues} critical issues")
        
        if major_issues > 5:
            blocking_reasons.append(f"{major_issues} major issues (max 5 allowed)")
        
        can_merge = len(blocking_reasons) == 0
        passed = all(r.success for r in results)
        
        logger.info(
            f"Quality gates summary:\n"
            f"  Passed: {passed}\n"
            f"  Can merge: {can_merge}\n"
            f"  Total issues: {len(all_issues)}\n"
            f"  Blocker: {blocker_issues}\n"
            f"  Critical: {critical_issues}\n"
            f"  Major: {major_issues}"
        )
        
        return QualityGateResult(
            passed=passed,
            results=results,
            total_issues=len(all_issues),
            blocker_issues=blocker_issues,
            critical_issues=critical_issues,
            major_issues=major_issues,
            can_merge=can_merge,
            blocking_reasons=blocking_reasons
        )


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def run_quality_gates(
    workspace_path: str,
    files: Optional[List[str]] = None
) -> QualityGateResult:
    """
    Convenience function to run quality gates.
    
    Args:
        workspace_path: Project root
        files: Files to analyze
        
    Returns:
        QualityGateResult with pass/fail status
    """
    gate = StaticAnalysisGate(workspace_path)
    return gate.run_quality_gates(files)
