"""
Semantic Verification Agent — LLM-based Content Verification

Reads the actual content of each candidate file and uses the LLM to determine
whether the file genuinely needs modification for the given ticket.

This is the critical gate between Evidence Ranking and the Planner.
Without this, irrelevant files (siblings, keyword matches, Java backend
files for a frontend bug) all pass through to the Planner unchecked.

How top AI IDEs do it:
  - Cursor: "Context Pruning" — reads file content, asks LLM to classify
  - Windsurf: "Relevance Verification" — batch LLM call on file snippets
  - Copilot Workspace: "File Relevance" — semantic check before planning

Author: Deepak Madgani
Date: August 2026
"""

import logging
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Any, Dict, Optional

logger = logging.getLogger(__name__)

# Max characters of file content to send to LLM per candidate
_MAX_SNIPPET_CHARS = 2000
# Max candidates to verify via LLM (cost control)
_MAX_LLM_VERIFIED = 20


@dataclass
class SemanticVerificationResult:
    """Result of semantic verification for a single candidate file."""
    file_path: str
    ranking_score: float
    semantic_relevance_score: float
    decision: str  # "include" | "exclude"
    reason: str


class SemanticVerificationAgent:
    """
    Verifies candidate files against ticket requirements using LLM-based
    content analysis.

    For each candidate file:
      1. Reads the first ~2000 chars of the file
      2. Asks the LLM: "Does this file need to be MODIFIED to fix this ticket?"
      3. Returns include/exclude with a reason

    This prevents irrelevant files (siblings with no bug, Java DTOs,
    constants files) from reaching the Planner.
    """

    def __init__(self, workspace_path: Optional[str] = None):
        self._workspace_path = workspace_path
        self._llm = None

    def _get_llm(self):
        """Lazy-load LLM to avoid import errors at module level."""
        if self._llm is None:
            try:
                from aviator.services.llm import LLMRegistry
                self._llm = LLMRegistry.get_llm()
            except Exception as e:
                logger.warning(f"[SemanticVerification] Could not load LLM: {e}")
        return self._llm

    def _read_file_snippet(self, file_path: str, evidence_lines: Optional[List[int]] = None) -> str:
        """Read the RELEVANT sections of a file for LLM verification.
        
        Strategy (like top AI IDEs):
        1. File signature (first 30 lines) — imports, class/function declarations
        2. Evidence-matched regions (±15 lines around each evidence hit)
        3. If no evidence, read the next structural section
        """
        if not self._workspace_path:
            return ""
        try:
            full_path = Path(self._workspace_path) / file_path
            if not full_path.exists() or not full_path.is_file():
                return ""
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()

            # Strategy: Read the RELEVANT sections, not just the first N chars.
            # 1. File signature (first 30 lines) — imports, class declarations
            # 2. Evidence-matched regions (±15 lines around each evidence hit)
            # 3. If no evidence lines, read key structural parts

            parts: List[str] = []
            used_chars = 0

            # Part 1: File signature (imports, class/function declarations)
            sig_end = min(30, len(lines))
            sig = "\n".join(lines[:sig_end])
            parts.append(f"[FILE SIGNATURE lines 1-{sig_end}]\n{sig}")
            used_chars += len(sig)

            # Part 2: Evidence-matched regions
            if evidence_lines:
                for eline in sorted(set(evidence_lines))[:5]:
                    if used_chars >= _MAX_SNIPPET_CHARS:
                        break
                    start = max(0, eline - 15)
                    end = min(len(lines), eline + 15)
                    # Skip if overlaps with signature
                    if start < sig_end:
                        start = sig_end
                    if start >= end:
                        continue
                    region = "\n".join(lines[start:end])
                    parts.append(f"[EVIDENCE REGION lines {start+1}-{end}]\n{region}")
                    used_chars += len(region)
            elif len(lines) > sig_end:
                # Part 3: No evidence lines — read middle structural section
                # Look for class/function definitions in the rest of the file
                mid_start = sig_end
                mid_end = min(len(lines), sig_end + 50)
                remaining_budget = _MAX_SNIPPET_CHARS - used_chars
                if remaining_budget > 200:
                    mid_text = "\n".join(lines[mid_start:mid_end])[:remaining_budget]
                    parts.append(f"[BODY lines {mid_start+1}-{mid_end}]\n{mid_text}")

            return "\n\n".join(parts)[:_MAX_SNIPPET_CHARS]
        except Exception as e:
            logger.debug(f"Could not read {file_path}: {e}")
        return ""

    def verify_candidates(
        self,
        ticket: Any,
        candidates: List[Any],
        evidence_items: Optional[List[Any]] = None,
        top_n: int = 20,
    ) -> List[SemanticVerificationResult]:
        """
        Verify a list of ranked candidate files against the ticket.

        Uses LLM to read each file's content and determine if it genuinely
        needs modification. Falls back to pass-through if LLM is unavailable.
        """
        results: List[SemanticVerificationResult] = []
        llm = self._get_llm()

        if not llm or not self._workspace_path:
            logger.warning(
                "[SemanticVerification] LLM or workspace not available — "
                "falling back to pass-through mode"
            )
            return self._passthrough(candidates, top_n)

        ticket_title = getattr(ticket, "title", "") or ""
        ticket_desc = getattr(ticket, "description", "") or ""

        # Collect evidence context per file — snippets AND line numbers
        evidence_by_file: Dict[str, List[str]] = {}
        evidence_lines_by_file: Dict[str, List[int]] = {}
        for e in (evidence_items or []):
            fp = getattr(e, "file_path", "")
            if fp:
                snippet = getattr(e, "content_snippet", "")[:200]
                evidence_by_file.setdefault(fp, []).append(snippet)
                # Extract line number from evidence for targeted reading
                line_num = getattr(e, "line_start", None) or getattr(e, "line_number", None)
                if not line_num:
                    # Try to parse from snippet format "[i18n:'key'] line 31: ..."
                    import re
                    m = re.search(r'line\s+(\d+)', snippet)
                    if m:
                        line_num = int(m.group(1))
                if line_num and isinstance(line_num, int):
                    evidence_lines_by_file.setdefault(fp, []).append(line_num)

        eval_candidates = candidates[:min(top_n, _MAX_LLM_VERIFIED)]

        # Build file entries for LLM
        file_entries: List[dict] = []
        for idx, candidate in enumerate(eval_candidates):
            fp = getattr(candidate, "file_path", None) or getattr(candidate, "path", "unknown")
            score = getattr(candidate, "final_score", 0.0) or getattr(candidate, "ranking_score", 0.0)
            ev_lines = evidence_lines_by_file.get(fp, [])
            snippet = self._read_file_snippet(fp, evidence_lines=ev_lines)
            ev_snippets = evidence_by_file.get(fp, [])
            ev_text = "; ".join(ev_snippets[:3]) if ev_snippets else "No direct evidence"

            file_entries.append({
                "index": idx,
                "file_path": fp,
                "score": float(score),
                "snippet": snippet if snippet else "(file not readable)",
                "evidence": ev_text[:300],
            })

        # Call LLM
        try:
            llm_results = self._batch_verify(llm, ticket_title, ticket_desc, file_entries)
        except Exception as e:
            logger.warning(
                f"[SemanticVerification] LLM batch verify failed: {e} — "
                f"falling back to pass-through"
            )
            return self._passthrough(candidates, top_n)

        # Map results
        for idx, candidate in enumerate(eval_candidates):
            fp = getattr(candidate, "file_path", None) or getattr(candidate, "path", "unknown")
            score = getattr(candidate, "final_score", 0.0) or getattr(candidate, "ranking_score", 0.0)

            llm_dec = llm_results.get(idx, {})
            decision = llm_dec.get("decision", "include")
            reason = llm_dec.get("reason", "LLM verification")
            sem_score = float(score) if decision == "include" else float(score) * 0.3

            results.append(SemanticVerificationResult(
                file_path=fp,
                ranking_score=float(score),
                semantic_relevance_score=sem_score,
                decision=decision,
                reason=reason,
            ))

        included = len([r for r in results if r.decision == "include"])
        excluded = len([r for r in results if r.decision != "include"])
        logger.info(
            f"[SemanticVerification] Verified {len(results)} candidates: "
            f"{included} included, {excluded} excluded"
        )
        for r in results:
            logger.info(
                f"  [{r.decision:7s}] score={r.ranking_score:.2f} "
                f"sem={r.semantic_relevance_score:.2f} {r.file_path} — {r.reason[:80]}"
            )

        return results

    def verify_single(
        self,
        ticket: Any,
        file_item: Any,
        existing_evidence: Dict[str, dict],
    ) -> "SemanticVerificationResult":
        """
        Verify a single file for relevance to the ticket.

        Used by the agentic search loop for per-file verification during
        evidence collection (instead of batch verification after ranking).

        Args:
            ticket: ValueEdgeTicket with .title and .description
            file_item: EvidenceItem with .file_path, .relevance_score, .content_snippet
            existing_evidence: Dict of already-verified files for context

        Returns:
            SemanticVerificationResult with:
              - file_path: str
              - ranking_score: float
              - semantic_relevance_score: float
              - decision: "include" | "exclude"
              - reason: str

        The decision vocabulary ("include"/"exclude") matches what the 4
        downstream consumers in workflow.py expect (lines 1711-1719, 1806,
        2020, and LOGGING_TEMPLATE.txt line 47).
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from ticket_to_code.llm_utils import llm_invoke

        fp = getattr(file_item, "file_path", "") or ""
        score = getattr(file_item, "relevance_score", 0.0) or 0.0
        snippet = getattr(file_item, "content_snippet", "") or ""

        # Read the actual file content for LLM verification
        file_content = self._read_file_snippet(fp)

        # Build context from existing evidence
        context_files = []
        for efp, ev in list(existing_evidence.items())[:5]:
            if ev.get("relevant"):
                context_files.append(f"  - {efp} (relevant: {ev.get('reason', '')[:60]})")

        context_str = "\n".join(context_files) if context_files else "  (no files verified yet)"

        ticket_title = getattr(ticket, "title", "") or ""
        ticket_desc = getattr(ticket, "description", "") or ""

        llm = self._get_llm()
        if not llm:
            # No LLM available - include by default
            return SemanticVerificationResult(
                file_path=fp,
                ranking_score=float(score),
                semantic_relevance_score=float(score),
                decision="include",
                reason="No LLM available for verification - included by default",
            )

        prompt = (
            f"TICKET:\n"
            f"  Title: {ticket_title}\n"
            f"  Description: {ticket_desc[:400]}\n\n"
            f"FILE TO VERIFY:\n"
            f"  Path: {fp}\n"
            f"  Evidence snippet: {snippet[:200]}\n\n"
            f"FILE CONTENT (first sections):\n"
            f"{file_content[:2000]}\n\n"
            f"ALREADY VERIFIED FILES:\n{context_str}\n\n"
            f"Does this file need to be MODIFIED to fix the ticket?\n"
            f"Answer with a JSON object:\n"
            f'{{"decision": "include"|"exclude", "reason": "..."}}\n'
            f"RULES:\n"
            f"- include = this file's code must change to fix the ticket\n"
            f"- exclude = this file is related but doesn't need code changes\n"
            f"- Be precise. Test files, constants, DTOs that won't change = exclude\n"
            f"Return ONLY the JSON."
        )

        try:
            from ticket_to_code.json_utils import parse_llm_json

            messages = [
                SystemMessage(content="You are a precise code reviewer verifying file relevance."),
                HumanMessage(content=prompt),
            ]
            response = llm_invoke(llm, messages)
            text = response.content if hasattr(response, "content") else str(response)
            result = parse_llm_json(text)

            decision = result.get("decision", "include") if isinstance(result, dict) else "include"
            reason = result.get("reason", "LLM verification") if isinstance(result, dict) else "LLM verification"

            if decision not in ("include", "exclude"):
                decision = "include"

            sem_score = float(score) if decision == "include" else float(score) * 0.3

            logger.info(
                f"  [verify_single] [{decision:7s}] {fp} - {reason[:80]}"
            )

            return SemanticVerificationResult(
                file_path=fp,
                ranking_score=float(score),
                semantic_relevance_score=sem_score,
                decision=decision,
                reason=reason,
            )
        except Exception as e:
            logger.warning(f"[verify_single] Failed for {fp}: {e} - including by default")
            return SemanticVerificationResult(
                file_path=fp,
                ranking_score=float(score),
                semantic_relevance_score=float(score),
                decision="include",
                reason=f"Verification error ({e}) - included by default",
            )

    def _batch_verify(
        self,
        llm: Any,
        ticket_title: str,
        ticket_desc: str,
        file_entries: List[dict],
    ) -> Dict[int, dict]:
        """
        Single LLM call to verify all candidate files at once.
        Returns dict mapping file index -> {"decision": "include"|"exclude", "reason": "..."}
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from ticket_to_code.llm_utils import llm_invoke

        system_prompt = (
            "You are a precise code reviewer. Your job is to determine which files "
            "actually need to be MODIFIED to fix a given ticket.\n\n"
            "For each file, you will see its path, ranking score, a code snippet, "
            "and evidence context.\n\n"
            "RULES:\n"
            "1. 'include' ONLY if modifying this file is NECESSARY to fix the ticket.\n"
            "2. 'exclude' if it is merely RELATED but does not need code changes.\n"
            "3. Files that just contain the keyword from the ticket but don't contain "
            "the actual bug/feature logic should be EXCLUDED.\n"
            "4. Constants files, DTOs, entity classes that don't need structural changes -> EXCLUDE.\n"
            "5. Files found only via 'component_group' or 'visual_chain' with no direct evidence -> verify carefully.\n\n"
            "RESPOND with JSON ONLY. No markdown. Format:\n"
            '[\n'
            '  {"index": 0, "decision": "include", "reason": "Contains the validation logic that needs fixing"},\n'
            '  {"index": 1, "decision": "exclude", "reason": "Just a DTO, no logic changes needed"}\n'
            ']\n'
        )

        files_text = ""
        for entry in file_entries:
            files_text += (
                f"\n--- File {entry['index']}: {entry['file_path']} "
                f"(score={entry['score']:.2f}) ---\n"
                f"Evidence: {entry['evidence']}\n"
                f"Content:\n{entry['snippet']}\n"
            )

        user_prompt = (
            f"TICKET: {ticket_title}\n"
            f"DESCRIPTION: {ticket_desc[:500]}\n\n"
            f"CANDIDATE FILES TO VERIFY:\n{files_text}\n\n"
            "For each file above, decide: does this file need to be MODIFIED to fix this ticket?\n"
            "Respond with JSON array only."
        )

        response = llm_invoke(llm, [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])

        # Parse response
        try:
            from ticket_to_code.json_utils import parse_llm_json
            decisions = parse_llm_json(response.content, expect_list=True)
        except Exception:
            match = re.search(r'\[.*\]', response.content, re.DOTALL)
            if match:
                decisions = json.loads(match.group(0))
            else:
                logger.warning("[SemanticVerification] Could not parse LLM response")
                return {}

        result_map: Dict[int, dict] = {}
        if isinstance(decisions, list):
            for d in decisions:
                if isinstance(d, dict) and "index" in d:
                    result_map[d["index"]] = {
                        "decision": d.get("decision", "include"),
                        "reason": d.get("reason", ""),
                    }

        return result_map

    def _passthrough(
        self,
        candidates: List[Any],
        top_n: int,
    ) -> List[SemanticVerificationResult]:
        """Fallback: include all candidates without LLM verification."""
        results = []
        for candidate in candidates[:top_n]:
            fp = getattr(candidate, "file_path", None) or getattr(candidate, "path", "unknown")
            score = getattr(candidate, "final_score", 0.0) or getattr(candidate, "ranking_score", 0.0)
            results.append(SemanticVerificationResult(
                file_path=fp,
                ranking_score=float(score),
                semantic_relevance_score=float(score),
                decision="include",
                reason="Pass-through (LLM unavailable)",
            ))
        logger.info(
            f"[SemanticVerification] Pass-through: {len(results)} candidates included"
        )
        return results

