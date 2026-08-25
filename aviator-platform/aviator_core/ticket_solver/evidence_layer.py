"""Evidence Layer — converts raw Observations into scored Evidence and Theories.

This is the "sense-making" layer that sits between actions and the BeliefState.
Every observation passes through here before updating beliefs.

Flow:
    Observation (raw) → EvidenceExtractor → Evidence[] (scored) → TheoryManager → Theory updates
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aviator_core.ticket_solver.reasoning import (
    BeliefState,
    Evidence,
    EvidenceType,
    Observation,
    ObservationType,
    Theory,
    TheoryStatus,
)

logger = logging.getLogger(__name__)


class EvidenceExtractor:
    """Extracts structured Evidence from raw Observations.

    Rules are deterministic — zero LLM tokens. Each observation type
    has specific extraction logic.
    """

    _counter: int = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"ev_{self._counter:03d}"

    def extract(self, obs: Observation) -> list[Evidence]:
        """Extract evidence from an observation. Returns zero or more Evidence objects."""
        if obs.error or obs.is_empty:
            return []

        extractors = {
            ObservationType.GREP_RESULT: self._from_grep,
            ObservationType.FILE_READ: self._from_file_read,
            ObservationType.BUILD_RESULT: self._from_build,
            ObservationType.SYMBOL_SEARCH: self._from_symbol_search,
            ObservationType.CALLER_SEARCH: self._from_caller_search,
            ObservationType.CONFIG_READ: self._from_config_read,
        }

        extractor = extractors.get(obs.observation_type)
        if extractor:
            return extractor(obs)

        logger.debug("No evidence extractor for observation type: %s", obs.observation_type)
        return []

    def _from_grep(self, obs: Observation) -> list[Evidence]:
        """Extract evidence from grep results."""
        evidence: list[Evidence] = []
        matches = obs.raw_data if isinstance(obs.raw_data, list) else obs.raw_data.get("grep_matches", [])

        for match in matches:
            file_path = match.get("file", match.get("relative_path", ""))
            line_num = match.get("line", 0)
            content = match.get("content", "")

            evidence.append(Evidence(
                id=self._next_id(),
                evidence_type=EvidenceType.FILE_CONTAINS_PATTERN,
                claim=f"Pattern found in {file_path}:{line_num}: {content[:80]}",
                confidence=0.8,  # Grep is reliable — it found what we searched for
                source_observations=[obs.id],
                file_path=file_path,
                line_number=line_num,
                metadata={"content": content},
            ))

        # If grep found nothing, that's also evidence
        if not matches:
            evidence.append(Evidence(
                id=self._next_id(),
                evidence_type=EvidenceType.FILE_MISSING_PATTERN,
                claim=f"Pattern NOT found by {obs.action_name}",
                confidence=0.7,  # Absence is slightly less certain (maybe wrong pattern)
                source_observations=[obs.id],
            ))

        return evidence

    def _from_file_read(self, obs: Observation) -> list[Evidence]:
        """Extract evidence from file content reads."""
        evidence: list[Evidence] = []
        files = obs.raw_data if isinstance(obs.raw_data, list) else obs.raw_data.get("files", [])

        for file_data in files:
            path = file_data.get("path", "")
            content = file_data.get("content", "")

            # Evidence: file exists and contains code
            evidence.append(Evidence(
                id=self._next_id(),
                evidence_type=EvidenceType.METHOD_EXISTS,
                claim=f"File read successfully: {path} ({len(content)} chars)",
                confidence=0.9,
                source_observations=[obs.id],
                file_path=path,
                metadata={"content_length": len(content)},
            ))

            # Try to identify owner candidates (files with relevant logic)
            if self._looks_like_logic_file(content):
                evidence.append(Evidence(
                    id=self._next_id(),
                    evidence_type=EvidenceType.OWNER_CANDIDATE,
                    claim=f"Potential target file: {path}",
                    confidence=0.6,
                    source_observations=[obs.id],
                    file_path=path,
                ))

        return evidence

    def _from_build(self, obs: Observation) -> list[Evidence]:
        """Extract evidence from build results."""
        build_data = obs.raw_data if isinstance(obs.raw_data, dict) else {"output": str(obs.raw_data)}
        passed = build_data.get("passed", False)

        if passed:
            return [Evidence(
                id=self._next_id(),
                evidence_type=EvidenceType.BUILD_PASSES,
                claim="Build completed successfully",
                confidence=1.0,
                source_observations=[obs.id],
            )]
        else:
            errors = build_data.get("errors", str(build_data.get("output", "")))
            return [Evidence(
                id=self._next_id(),
                evidence_type=EvidenceType.BUILD_FAILS,
                claim=f"Build failed: {str(errors)[:200]}",
                confidence=1.0,
                source_observations=[obs.id],
                metadata={"errors": errors},
            )]

    def _from_symbol_search(self, obs: Observation) -> list[Evidence]:
        """Extract evidence from symbol search results."""
        evidence: list[Evidence] = []
        symbols = obs.raw_data if isinstance(obs.raw_data, list) else obs.raw_data.get("symbols", [])

        for sym in symbols:
            evidence.append(Evidence(
                id=self._next_id(),
                evidence_type=EvidenceType.METHOD_EXISTS,
                claim=f"Symbol found: {sym.get('name', '')} in {sym.get('file', '')}",
                confidence=0.9,
                source_observations=[obs.id],
                file_path=sym.get("file", ""),
                metadata=sym,
            ))

        return evidence

    def _from_caller_search(self, obs: Observation) -> list[Evidence]:
        """Extract evidence from caller search results."""
        evidence: list[Evidence] = []
        callers = obs.raw_data if isinstance(obs.raw_data, list) else obs.raw_data.get("callers", [])

        for caller in callers:
            evidence.append(Evidence(
                id=self._next_id(),
                evidence_type=EvidenceType.METHOD_CALLED_BY,
                claim=f"Called by: {caller.get('caller', '')} in {caller.get('file', '')}",
                confidence=0.85,
                source_observations=[obs.id],
                file_path=caller.get("file", ""),
                metadata=caller,
            ))

        # Build dependency chain evidence
        if callers:
            chain = [c.get("file", "") for c in callers if c.get("file")]
            evidence.append(Evidence(
                id=self._next_id(),
                evidence_type=EvidenceType.DEPENDENCY_CHAIN,
                claim=f"Dependency chain: {len(chain)} callers found",
                confidence=0.85,
                source_observations=[obs.id],
                metadata={"chain": chain},
            ))

        return evidence

    def _from_config_read(self, obs: Observation) -> list[Evidence]:
        """Extract evidence from config file reads."""
        config = obs.raw_data if isinstance(obs.raw_data, dict) else {}
        evidence: list[Evidence] = []

        for key, value in config.items():
            evidence.append(Evidence(
                id=self._next_id(),
                evidence_type=EvidenceType.CONFIG_VALUE_IS,
                claim=f"Config: {key} = {value}",
                confidence=0.95,
                source_observations=[obs.id],
                metadata={"key": key, "value": value},
            ))

        return evidence

    @staticmethod
    def _looks_like_logic_file(content: str) -> bool:
        """Heuristic: does this file contain business logic?"""
        logic_indicators = ["class ", "def ", "function ", "public ", "private ", "void "]
        return any(indicator in content for indicator in logic_indicators)


class TheoryManager:
    """Manages theory creation, updates, and selection.

    Theories are hypotheses about the root cause and fix. They evolve
    as evidence accumulates — some get confirmed, others abandoned.
    """

    _counter: int = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"th_{self._counter:03d}"

    def create_initial_theories(
        self,
        ticket_title: str,
        ticket_description: str,
    ) -> list[Theory]:
        """Create initial theories from the ticket text alone (before any search)."""
        theories: list[Theory] = []

        # Theory 1: The obvious interpretation
        theories.append(Theory(
            id=self._next_id(),
            hypothesis=f"Direct fix implied by ticket: {ticket_title}",
            confidence=0.5,
            status=TheoryStatus.PROPOSED,
        ))

        return theories

    def update_with_evidence(
        self,
        belief: BeliefState,
        new_evidence: list[Evidence],
    ) -> None:
        """Update all theories with new evidence and adjust belief.

        Links evidence to theories by checking if evidence file paths
        match theory target files (supports) or if build failures
        contradict active theories (contradicts).
        """
        for ev in new_evidence:
            # Link evidence to theories BEFORE adding to belief
            self._link_evidence_to_theories(ev, belief.theories)

            belief.add_evidence(ev)

            # Update owner candidates from file-related evidence
            if ev.file_path and ev.evidence_type in (
                EvidenceType.FILE_CONTAINS_PATTERN,
                EvidenceType.OWNER_CANDIDATE,
                EvidenceType.METHOD_EXISTS,
            ):
                current = belief.owner_candidates.get(ev.file_path, 0.0)
                belief.owner_candidates[ev.file_path] = min(1.0, current + ev.confidence * 0.15)

            # Contradictions from build failures
            if ev.evidence_type == EvidenceType.BUILD_FAILS:
                belief.contradictions.append(f"Build failed: {ev.claim[:100]}")

    def _link_evidence_to_theories(
        self,
        evidence: Evidence,
        theories: list[Theory],
    ) -> None:
        """Populate supports_theories and contradicts_theories in evidence metadata.

        Rules:
        - FILE_CONTAINS_PATTERN on a theory's target file → supports
        - FILE_MISSING_PATTERN on a theory's target file → contradicts
        - BUILD_PASSES after patch → supports active theories
        - BUILD_FAILS after patch → contradicts active theories
        - OWNER_CANDIDATE matching target → supports
        """
        supports: list[str] = []
        contradicts: list[str] = []

        for theory in theories:
            if theory.status in (TheoryStatus.ABANDONED, TheoryStatus.CONTRADICTED):
                continue

            # File-based linking
            if evidence.file_path and theory.target_files:
                if evidence.file_path in theory.target_files:
                    if evidence.evidence_type in (
                        EvidenceType.FILE_CONTAINS_PATTERN,
                        EvidenceType.METHOD_EXISTS,
                        EvidenceType.OWNER_CANDIDATE,
                    ):
                        supports.append(theory.id)
                    elif evidence.evidence_type == EvidenceType.FILE_MISSING_PATTERN:
                        contradicts.append(theory.id)

            # Build-based linking
            if evidence.evidence_type == EvidenceType.BUILD_PASSES:
                supports.append(theory.id)
            elif evidence.evidence_type == EvidenceType.BUILD_FAILS:
                contradicts.append(theory.id)

        # Write links into evidence metadata
        evidence.metadata["supports_theories"] = supports
        evidence.metadata["contradicts_theories"] = contradicts

    def propose_theory_from_evidence(
        self,
        belief: BeliefState,
        evidence_batch: list[Evidence],
    ) -> Theory | None:
        """If evidence points to a clear pattern, propose a new theory."""
        # Check if multiple pieces of evidence point to the same file
        file_counts: dict[str, int] = {}
        for ev in evidence_batch:
            if ev.file_path:
                file_counts[ev.file_path] = file_counts.get(ev.file_path, 0) + 1

        # If 3+ evidence items point to the same file, propose it as the target
        for file_path, count in file_counts.items():
            if count >= 3:
                theory = Theory(
                    id=self._next_id(),
                    hypothesis=f"Root cause is in {file_path} — multiple evidence items converge",
                    target_files=[file_path],
                    confidence=0.6,
                    status=TheoryStatus.SUPPORTED,
                    supporting_evidence=[ev.id for ev in evidence_batch if ev.file_path == file_path],
                )
                belief.add_theory(theory)
                logger.info("TheoryManager: proposed theory %s for %s", theory.id, file_path)
                return theory

        return None
