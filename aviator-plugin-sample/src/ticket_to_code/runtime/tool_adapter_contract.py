"""Tool adapter interface contract for the orchestrator.

Allows the same orchestrator core to run on local CLI tools, IDE tools, or API tools.
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol

from ticket_to_code.runtime.intake_contract import GroundTruthTicket


class ToolAdapter(Protocol):
    """Portable adapter contract used by TicketOS-like orchestrators."""

    def search_symbols_and_literals(self, query: str) -> List[Dict[str, Any]]:
        """Return candidate files/symbols/literals relevant to the ticket."""

    def find_existing_authoritative_source(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Pick existing source-of-truth from discovered candidates."""

    def create_edit_plan(
        self,
        source: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        ticket: GroundTruthTicket,
    ) -> Dict[str, Any]:
        """Return minimal scoped edit plan."""

    def apply_patch_plan(self, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply edits and return changed file records with path and symbols."""

    def check_forbidden_files_not_touched(
        self,
        forbidden_patterns: List[str],
        changed_files: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return {'name','result','hard_gate','evidence'} check record."""

    def check_expected_coverage(
        self,
        expected_patterns: List[str],
        changed_files: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return expected-scope coverage check record."""

    def check_diagnostics_changed_files_only(
        self,
        changed_files: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return diagnostics check record."""

    def check_build_signal(self) -> Dict[str, Any]:
        """Return non-blocking build signal check record for phase-1 policy."""
