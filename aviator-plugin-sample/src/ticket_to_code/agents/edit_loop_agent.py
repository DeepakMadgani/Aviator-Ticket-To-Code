"""
Edit Loop Agent — The "Claude Code / Devin" style free-form edit loop.

Architecture contrast:
  OLD (current):  Plan N files → Generate each in order → Full build → Fix loop
  NEW (this):     Understand ticket → While not solved: pick next file → read it
                  fully → make targeted edit → verify immediately → continue

Key properties:
  - NO fixed plan. The agent decides what to edit next based on current state.
  - Unbounded iterations (bounded by MAX_ITER safety cap, default 25)
  - Every file write is immediately verified with the TypeScript/Java compiler
  - When verification fails, the error is fixed BEFORE moving to the next file
  - The LLM always reads the CURRENT file content (not a cached snapshot)

This is how Claude Code, Devin, and Cursor Composer actually work.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ticket_to_code.models import (
        ValueEdgeTicket,
        StructuredRequirements,
        ArchitecturalPlan,
    )

from ticket_to_code.agents.lsp_client import WorkspaceSymbolIndex, ClassMembers

logger = logging.getLogger(__name__)


MAX_ITER = 8           # safety cap — never exceed this many file edits
MAX_FIX_PER_FILE = 2   # max inline fix attempts per file before moving on


@dataclass
class EditDecision:
    """The agent's decision about what to edit next."""
    file_path: str
    reason: str
    edit_description: str
    is_new_file: bool = False
    priority: int = 5  # 1=critical, 10=optional


@dataclass
class EditResult:
    """Result of one file edit + verification cycle."""
    file_path: str
    content_before: str
    content_after: str
    compile_errors: list[str] = field(default_factory=list)
    compile_clean: bool = False
    fix_applied: bool = False
    skipped: bool = False
    reason: str = ""


class EditLoopAgent:
    """
    Drives an autonomous edit loop that mirrors how AI IDEs work:

    1. Assess the current state of the codebase vs. ticket requirements
    2. Choose the highest-impact file to edit next
    3. Read the full current file content (always fresh from disk)
    4. Generate a targeted, minimal edit
    5. Verify immediately with the language compiler
    6. If errors → fix inline before moving on
    7. Repeat until the ticket requirements are fully implemented

    This replaces the rigid plan-then-execute approach with an adaptive loop
    that can discover and fix issues as they arise — exactly like a developer
    working in a terminal with `tsc --watch` running.
    """

    def __init__(
        self,
        workspace_path: str,
        llm,
        symbol_index: Optional[WorkspaceSymbolIndex] = None,
    ):
        self.workspace_path = Path(workspace_path)
        self.llm = llm
        self.symbol_index = symbol_index or WorkspaceSymbolIndex(workspace_path)
        self._ng_root: Optional[Path] = None
        self._written_files: dict[str, str] = {}  # path → last written content

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(
        self,
        ticket: "ValueEdgeTicket",
        requirements: "StructuredRequirements",
        initial_plan: Optional["ArchitecturalPlan"] = None,
        code_rag_context: str = "",
        existing_file_map: Optional[dict[str, str]] = None,
    ) -> dict:
        """
        Run the free-form edit loop until the ticket is solved or MAX_ITER reached.

        Returns:
            {
                "generated_files": {path: content},
                "edit_results": [EditResult],
                "final_compile_errors": [str],
                "iterations": int,
                "solved": bool,
            }
        """
        logger.info("\n" + "=" * 70)
        logger.info(" EDIT LOOP AGENT: Starting free-form edit loop")
        logger.info(f"   Ticket: {ticket.ticket_id} — {ticket.title}")
        logger.info(f"   MAX_ITER: {MAX_ITER}")
        logger.info("=" * 70)

        if existing_file_map:
            self._written_files.update(existing_file_map)

        edit_results: list[EditResult] = []
        iteration = 0
        files_edited: set[str] = set()

        # Seed the initial file queue from the architectural plan (if provided)
        # This gives the loop a starting direction without locking the plan.
        pending_queue: list[EditDecision] = []
        if initial_plan and initial_plan.tasks:
            for t in initial_plan.tasks:
                if getattr(getattr(t, "task_type", None), "value", "") != "read_only":
                    pending_queue.append(EditDecision(
                        file_path=t.file_path,
                        reason=f"Planner task: {t.title}",
                        edit_description=t.description or t.title,
                        is_new_file=getattr(getattr(t, "task_type", None), "value", "") == "create",
                        priority=3,
                    ))

        while iteration < MAX_ITER:
            iteration += 1
            logger.info(f"\n--- EDIT LOOP iteration {iteration}/{MAX_ITER} ---")

            # Pick next file to edit
            decision = self._decide_next_edit(
                ticket, requirements, pending_queue, files_edited, code_rag_context
            )
            if decision is None:
                logger.info("  Agent decided: ticket fully implemented. Loop complete.")
                break

            logger.info(f"  Next edit: {decision.file_path}")
            logger.info(f"  Reason: {decision.reason}")

            # Remove from pending queue if it was there
            pending_queue = [q for q in pending_queue if q.file_path != decision.file_path]

            # Read current file content (ALWAYS from disk — never a stale cache)
            current_content = self._read_file(decision.file_path)
            if current_content is None and not decision.is_new_file:
                logger.warning(f"  File not found on disk: {decision.file_path} — skipping")
                edit_results.append(EditResult(
                    file_path=decision.file_path,
                    content_before="",
                    content_after="",
                    skipped=True,
                    reason="file not found",
                ))
                continue

            # Get LSP context for this file (exact class members)
            lsp_context = self._get_lsp_context(decision.file_path, current_content)

            # Generate the edit
            new_content = self._generate_edit(
                decision=decision,
                current_content=current_content or "",
                ticket=ticket,
                requirements=requirements,
                code_rag_context=code_rag_context,
                lsp_context=lsp_context,
            )

            if new_content is None or new_content == current_content:
                logger.info(f"  No change produced for {decision.file_path} — skipping")
                edit_results.append(EditResult(
                    file_path=decision.file_path,
                    content_before=current_content or "",
                    content_after=current_content or "",
                    skipped=True,
                    reason="no change generated",
                ))
                continue

            # Write the file
            self._write_file(decision.file_path, new_content)
            files_edited.add(decision.file_path)

            # Immediately verify
            compile_errors = self._verify_file(decision.file_path)

            fix_applied = False
            if compile_errors:
                logger.warning(
                    f"  ⚠️ Compile errors after writing {decision.file_path} "
                    f"({len(compile_errors)} error(s))"
                )
                # Fix inline before moving on
                for fix_attempt in range(MAX_FIX_PER_FILE):
                    logger.info(f"  Inline fix attempt {fix_attempt + 1}/{MAX_FIX_PER_FILE}")
                    fixed = self._fix_errors(
                        decision.file_path, new_content, compile_errors,
                        ticket, requirements, code_rag_context, lsp_context
                    )
                    if fixed and fixed != new_content:
                        self._write_file(decision.file_path, fixed)
                        new_content = fixed
                        fix_applied = True
                        remaining_errors = self._verify_file(decision.file_path)
                        if not remaining_errors:
                            logger.info(f"  ✅ Inline fix resolved all errors")
                            compile_errors = []
                            break
                        compile_errors = remaining_errors
                    else:
                        logger.info(f"  Fix attempt produced no change")
                        break

            compile_clean = not bool(compile_errors)
            if compile_clean:
                logger.info(f"  ✅ {decision.file_path} — clean compile")
            else:
                logger.warning(
                    f"  ⚠️ {decision.file_path} — {len(compile_errors)} error(s) remain "
                    f"(will be addressed by full build phase)"
                )

            edit_results.append(EditResult(
                file_path=decision.file_path,
                content_before=current_content or "",
                content_after=new_content,
                compile_errors=compile_errors,
                compile_clean=compile_clean,
                fix_applied=fix_applied,
            ))

            # Update the symbol index cache so subsequent edits see fresh types
            ext = Path(decision.file_path).suffix.lower()
            if ext in (".ts", ".tsx", ".java", ".kt"):
                self.symbol_index.get_members_for_file(decision.file_path)

        # Final compile check
        ts_files = [fp for fp in files_edited if fp.endswith((".ts", ".tsx"))]
        final_errors = self.symbol_index.get_ts_diagnostics(ts_files) if ts_files else []

        solved = not bool(final_errors)
        if solved:
            logger.info(f"\n✅ EDIT LOOP COMPLETE: Ticket solved in {iteration} iteration(s)")
        else:
            logger.warning(
                f"\n⚠️ EDIT LOOP COMPLETE: {len(final_errors)} compile error(s) remain "
                f"after {iteration} iteration(s) — full build phase will handle them"
            )

        return {
            "generated_files": dict(self._written_files),
            "edit_results": edit_results,
            "final_compile_errors": final_errors,
            "iterations": iteration,
            "solved": solved,
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _decide_next_edit(
        self,
        ticket: "ValueEdgeTicket",
        requirements: "StructuredRequirements",
        pending_queue: list[EditDecision],
        files_edited: set[str],
        code_rag_context: str,
    ) -> Optional[EditDecision]:
        """
        Ask the LLM: given the current codebase state, what is the NEXT file
        that needs to be edited to implement the ticket?

        Returns None when the ticket is fully implemented.
        """
        # If pending queue has items not yet edited, pick the highest priority one
        not_yet_edited = [q for q in pending_queue if q.file_path not in files_edited]
        if not_yet_edited:
            # Sort by priority (lower number = higher priority)
            not_yet_edited.sort(key=lambda q: q.priority)
            return not_yet_edited[0]

        # All planned files done — ask the agent if anything is still missing
        if not files_edited:
            return None

        # Build a summary of what's been done
        done_summary = "\n".join(f"  ✅ {fp}" for fp in sorted(files_edited))
        current_file_states = self._summarize_written_files()

        from ticket_to_code.llm_utils import llm_invoke
        from langchain_core.messages import SystemMessage, HumanMessage

        system_prompt = """You are a senior developer assessing whether a ticket is fully implemented.
Given the ticket requirements and the files that have been modified, determine:
1. Is the ticket FULLY implemented? If yes, respond with just: DONE
2. If NOT fully implemented, which single file should be edited next?

Respond with EITHER:
  DONE
OR:
  FILE: <exact relative file path>
  REASON: <why this file still needs changes>
  DESCRIPTION: <what specific change to make>

Be decisive. Do not suggest files that are already done unless they need further changes."""

        user_prompt = f"""TICKET: {ticket.title}
{ticket.description[:500]}

REQUIREMENTS:
{getattr(requirements, 'functional_requirements', '')[:400]}

FILES ALREADY MODIFIED:
{done_summary}

CURRENT STATE OF WRITTEN FILES:
{current_file_states[:2000]}

Is the ticket fully implemented? If not, what is the next file to edit?"""

        try:
            response = llm_invoke(self.llm, [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            text = response.content.strip()

            if text.startswith("DONE") or "fully implemented" in text.lower():
                return None

            file_m = re.search(r'FILE:\s*(.+)', text)
            reason_m = re.search(r'REASON:\s*(.+)', text)
            desc_m = re.search(r'DESCRIPTION:\s*(.+)', text, re.DOTALL)

            if not file_m:
                return None

            fp = file_m.group(1).strip().strip("`'\"")
            return EditDecision(
                file_path=fp,
                reason=reason_m.group(1).strip() if reason_m else "Agent identified missing change",
                edit_description=desc_m.group(1).strip()[:400] if desc_m else "Make required changes",
                priority=5,
            )
        except Exception as exc:
            logger.warning(f"  Agent decision failed: {exc}")
            return None

    def _generate_edit(
        self,
        decision: EditDecision,
        current_content: str,
        ticket: "ValueEdgeTicket",
        requirements: "StructuredRequirements",
        code_rag_context: str,
        lsp_context: str,
    ) -> Optional[str]:
        """Generate a targeted, minimal edit for the file."""
        from ticket_to_code.llm_utils import llm_invoke
        from langchain_core.messages import SystemMessage, HumanMessage

        ext = Path(decision.file_path).suffix.lower()
        is_html = ext in (".html", ".htm")
        is_create = decision.is_new_file or not current_content

        # Build session context (what's been written so far in this run)
        session_ctx = self._build_session_context(decision.file_path)

        system_prompt = f"""You are a senior developer making a TARGETED, MINIMAL edit to ONE file.

ABSOLUTE RULES:
1. ONLY modify the single file specified.
2. Make the SMALLEST change that satisfies the requirement.
3. Preserve ALL existing logic, methods, and imports not related to the change.
4. {"Output the COMPLETE new file content." if is_create else "Use SEARCH/REPLACE blocks — do NOT output the whole file."}

{"ANGULAR TEMPLATE RULE: Before writing any *ngIf or {{ }} binding, read the SIBLING CONTROLLER section in the context. Use ONLY property names that are DECLARED in that controller. Never invent new names." if is_html else ""}

SEARCH/REPLACE FORMAT:
<<<SEARCH>>>
<exact existing code>
<<<REPLACE>>>
<new code>
<<<END>>>

SEARCH/REPLACE RULES:
- Copy the EXACT existing lines including whitespace. Even one character difference will cause a mismatch.
- Prefer multiple small SEARCH/REPLACE blocks over one very large block.
- NEVER put the entire file content in a SEARCH block.
- Include enough context lines so the SEARCH block is unique in the file.
- You may use as many SEARCH/REPLACE blocks as needed — use the right size for each change."""

        # Smart file content: like top AI IDEs, read structure first then zoom in
        if current_content:
            content_len = len(current_content)
            if content_len <= 50000:
                # Small/medium files: show full content
                file_content_block = current_content
            else:
                # Large files: extract outline + relevant methods
                file_content_block = self._smart_extract_for_large_file(
                    current_content, decision.edit_description, decision.file_path
                )
        else:
            file_content_block = "(new file)"

        user_prompt = f"""TICKET: {ticket.title}
{ticket.description[:600]}

FILE TO EDIT: {decision.file_path}
REASON FOR EDIT: {decision.reason}
WHAT TO CHANGE: {decision.edit_description}

{lsp_context}

{session_ctx}

CURRENT FILE CONTENT:
```
{file_content_block}
```

Make the targeted edit now."""

        try:
            response = llm_invoke(self.llm, [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            return self._apply_search_replace(current_content or "", response.content, decision.is_new_file)
        except Exception as exc:
            logger.warning(f"  Edit generation failed for {decision.file_path}: {exc}")
            return None

    def _fix_errors(
        self,
        file_path: str,
        current_content: str,
        errors: list[str],
        ticket: "ValueEdgeTicket",
        requirements: "StructuredRequirements",
        code_rag_context: str,
        lsp_context: str,
    ) -> Optional[str]:
        """Fix compile errors with cross-file awareness and action-based validation.

        This runs in the FAILURE NODE after all planned files have been written.
        At this point we have a complete list of modified/created files, so cross-file
        fixes are safe — we won't accidentally overwrite a file before its task runs.
        """
        import json as _json_fix
        import os as _os_fix
        from ticket_to_code.llm_utils import llm_invoke
        from langchain_core.messages import SystemMessage, HumanMessage

        error_text = "\n".join(errors[:20])

        # ── Collect related files mentioned in errors ──
        _all_error_files: dict[str, str] = {file_path: current_content}
        for err_line in errors:
            ep = re.search(
                r'([^\s(]+\.(?:ts|tsx|js|jsx|java|kt|py))\s*[\(:\[]',
                err_line, re.IGNORECASE
            )
            if ep:
                ep_raw = ep.group(1).lstrip("/").replace("\\", "/")
                if ep_raw != file_path.replace("\\", "/") and ep_raw not in _all_error_files:
                    # Try to resolve: written this run → direct path → workspace scan
                    resolved_path, resolved_content = self._resolve_error_file(ep_raw)
                    if resolved_content and resolved_path not in _all_error_files:
                        _all_error_files[resolved_path] = resolved_content
                        logger.info(f"  Including related error file: {resolved_path}")

        # ── Also inject session-generated files imported by the primary file ──
        src_dir = str(Path(file_path).parent).replace("\\", "/")
        for imp_m in re.finditer(r"""from\s+['"](\.[']+)['"]""", current_content):
            for iext in (".ts", ".tsx"):
                irel = _os_fix.path.normpath(
                    _os_fix.path.join(src_dir, imp_m.group(1) + iext)
                ).replace("\\", "/").lower()
                if irel in self._written_files and irel not in _all_error_files:
                    _all_error_files[irel] = self._written_files[irel]

        # ── Build related files section ──
        other_files_section = ""
        for rf, rc in _all_error_files.items():
            if rf != file_path:
                other_files_section += f"\nRELATED FILE: {rf}\n```\n{rc[:3000]}\n```\n"

        # ── Ticket context so fixes preserve intent ──
        ticket_title = getattr(ticket, "title", "") or ""
        ticket_desc = (getattr(ticket, "description", "") or "")[:400]
        func_reqs = ""
        if requirements:
            fr = getattr(requirements, "functional_requirements", []) or []
            func_reqs = "\n".join(f"  - {r}" for r in fr[:5])

        system_prompt = (
            "You are fixing compile errors while PRESERVING the ticket's intended functionality.\n"
            "CRITICAL: Do NOT remove or stub out new functionality to make the code compile.\n"
            "If a method or property is missing, ADD it where it belongs.\n"
            "If a service call is wrong, fix the call to use the right service — not remove it.\n\n"
            "The root cause may be in a DIFFERENT file than where the error appears.\n"
            "Fix the actual root cause, not just the symptom.\n\n"
            "Return a JSON object:\n"
            "{\n"
            '  "reasoning": "root cause + which files need changing + how intent is preserved",\n'
            '  "fixes": [\n'
            '    {"file": "exact/relative/path.ts", "action": "modify", "content": "complete corrected file content"},\n'
            '    {"file": "new/file/if/needed.ts", "action": "create", "content": "complete new file content"},\n'
            '    ...\n'
            "  ]\n"
            "}\n"
            "Rules:\n"
            "- For each fix, set 'action' to 'modify' if the file already exists, or 'create' if it is a brand new file\n"
            "- Only use 'create' when a missing module/import/class truly needs a new file to exist\n"
            "- PREFER modifying existing files over creating new ones\n"
            "- Fix every file that needs to change (primary + root-cause files)\n"
            "- 'content' = COMPLETE file content, not a diff\n"
            "- Only fix files shown below unless you need to create a genuinely missing dependency\n"
            "- Respond with JSON only, no markdown"
        )

        user_prompt = (
            f"TICKET: {ticket_title}\n{ticket_desc}\n\n"
            + (f"FUNCTIONAL REQUIREMENTS (must still be satisfied after fix):\n{func_reqs}\n\n" if func_reqs else "")
            + f"COMPILE ERRORS:\n{error_text}\n\n"
            + f"{lsp_context}\n\n"
            + f"PRIMARY FILE: {file_path}\n```\n{current_content[:4000]}\n```\n"
            + other_files_section
            + "\nFix all errors without removing any new functionality. Return JSON."
        )

        # ── Feedback loop: ask LLM → validate → rejected? → tell LLM why → retry ──
        MAX_FIX_ATTEMPTS = 3
        rejection_history: list[str] = []  # accumulates across attempts
        primary_content = None

        for attempt in range(MAX_FIX_ATTEMPTS):
            # Build the prompt — include rejection feedback from previous attempts
            rejection_feedback = ""
            if rejection_history:
                rejection_feedback = (
                    "\n\n⚠️ YOUR PREVIOUS FIX ATTEMPT WAS PARTIALLY REJECTED:\n"
                    + "\n".join(f"  ❌ {r}" for r in rejection_history)
                    + "\n\nFix these issues in your next attempt. "
                    "If you tried to 'modify' a file that doesn't exist, either:\n"
                    "  1. Use 'action': 'create' if the file genuinely needs to be created\n"
                    "  2. Fix the issue in a DIFFERENT existing file instead\n"
                    "  3. Add the missing method/property in the file where the type is actually defined\n"
                )

            final_user_prompt = (
                user_prompt + rejection_feedback
            )

            try:
                response = llm_invoke(self.llm, [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=final_user_prompt),
                ])
                fix_raw = response.content if hasattr(response, "content") else str(response)

                # Parse JSON response
                json_m = re.search(r'\{.*\}', fix_raw, re.DOTALL)
                if not json_m:
                    logger.warning(f"  Attempt {attempt+1}: No JSON in response — falling back to SEARCH/REPLACE")
                    return self._apply_search_replace(current_content, fix_raw, False)

                fix_result = _json_fix.loads(json_m.group(0))
                fixes = fix_result.get("fixes", [])
                if not fixes:
                    logger.warning(f"  Attempt {attempt+1}: No fixes in JSON response")
                    if attempt < MAX_FIX_ATTEMPTS - 1:
                        rejection_history.append("You returned an empty 'fixes' array. Provide actual fixes.")
                        continue
                    return None

                logger.info(
                    f"  Attempt {attempt+1}: {len(fixes)} fix(es) — "
                    f"{fix_result.get('reasoning', '')[:120]}"
                )

                # ── Validate and apply each fix ──
                attempt_rejections: list[str] = []
                applied_count = 0

                for fix_item in fixes:
                    fix_path_raw = fix_item.get("file", "")
                    fix_content = fix_item.get("content", "")
                    fix_action = fix_item.get("action", "modify").lower().strip()

                    if not fix_path_raw or not fix_content:
                        continue

                    # Resolve the path
                    resolved_path = self._resolve_fix_path(fix_path_raw)
                    fix_abs = self.workspace_path / resolved_path
                    is_primary = (
                        resolved_path.replace("\\", "/").lower()
                        == file_path.replace("\\", "/").lower()
                    )

                    # Action-based validation
                    if fix_action == "modify":
                        if not fix_abs.exists() and not is_primary:
                            msg = (
                                f"action='modify' for '{resolved_path}' but file does NOT exist on disk. "
                                f"Either use action='create' if it truly needs to be created, "
                                f"or fix the issue in an existing file instead."
                            )
                            logger.warning(f"  ❌ {msg}")
                            attempt_rejections.append(msg)
                            continue
                    elif fix_action == "create":
                        if fix_abs.exists():
                            logger.info(
                                f"  action='create' but file already exists: "
                                f"{resolved_path} — treating as modify"
                            )
                        else:
                            logger.info(f"  ✅ LLM requested file creation: {resolved_path}")
                    else:
                        if not fix_abs.exists() and not is_primary:
                            msg = (
                                f"Unknown action='{fix_action}' for '{resolved_path}' "
                                f"and file does not exist. Use 'modify' or 'create'."
                            )
                            logger.warning(f"  ❌ {msg}")
                            attempt_rejections.append(msg)
                            continue

                    # Validation: no protected paths
                    fp_lower = resolved_path.lower()
                    if any(x in fp_lower for x in ("/dist/", "/node_modules/", "/.git/", "/target/")):
                        msg = f"Fix targets protected path: {resolved_path} — cannot modify."
                        logger.warning(f"  ❌ {msg}")
                        attempt_rejections.append(msg)
                        continue

                    # Validation: must be inside workspace
                    try:
                        fix_abs.resolve().relative_to(self.workspace_path.resolve())
                    except ValueError:
                        msg = f"Fix targets path outside workspace: {resolved_path}"
                        logger.warning(f"  ❌ {msg}")
                        attempt_rejections.append(msg)
                        continue

                    # ── All validations passed — write the fix ──
                    fix_abs.parent.mkdir(parents=True, exist_ok=True)
                    fix_abs.write_text(fix_content, encoding="utf-8")
                    self._written_files[resolved_path.replace("\\", "/").lower()] = fix_content
                    logger.info(
                        f"    ✏️  {fix_action.upper()}: {resolved_path} "
                        f"({len(fix_content)} chars)"
                    )
                    applied_count += 1

                    if is_primary:
                        primary_content = fix_content

                # ── Decide: all applied? some rejected? ──
                if attempt_rejections:
                    rejection_history.extend(attempt_rejections)
                    if applied_count == 0:
                        # ALL fixes were rejected — must retry
                        logger.warning(
                            f"  ⚠️ Attempt {attempt+1}: ALL {len(attempt_rejections)} "
                            f"fix(es) rejected — feeding back to LLM"
                        )
                        if attempt < MAX_FIX_ATTEMPTS - 1:
                            continue  # retry with feedback
                        else:
                            logger.warning(f"  ⚠️ All {MAX_FIX_ATTEMPTS} attempts exhausted")
                            return primary_content
                    else:
                        # Some applied, some rejected — partial success
                        logger.info(
                            f"  Attempt {attempt+1}: {applied_count} fix(es) applied, "
                            f"{len(attempt_rejections)} rejected"
                        )
                        return primary_content
                else:
                    # All fixes applied successfully
                    logger.info(f"  ✅ Attempt {attempt+1}: all {applied_count} fix(es) applied")
                    return primary_content

            except Exception as exc:
                logger.warning(f"  Attempt {attempt+1} failed: {exc}")
                if attempt < MAX_FIX_ATTEMPTS - 1:
                    rejection_history.append(f"Previous attempt threw an error: {str(exc)[:200]}")
                    continue

        # All attempts exhausted — fallback to simple SEARCH/REPLACE
        logger.warning(f"  All fix attempts exhausted — falling back to SEARCH/REPLACE")
        try:
            from ticket_to_code.llm_utils import llm_invoke
            from langchain_core.messages import SystemMessage, HumanMessage
            response = llm_invoke(self.llm, [
                SystemMessage(content="Fix compile errors using SEARCH/REPLACE blocks. Fix ONLY what the error requires."),
                HumanMessage(content=f"ERRORS:\n{error_text}\n\nFILE:\n```\n{current_content[:4000]}\n```"),
            ])
            return self._apply_search_replace(current_content, response.content, False)
        except Exception:
            return primary_content

    def _resolve_error_file(self, fp_raw: str) -> tuple[str, Optional[str]]:
        """Resolve a file path from a compiler error to actual workspace file + content.
        Priority: written-this-run → direct path → workspace scan.
        """
        fp = fp_raw.replace("\\", "/")
        target_name = Path(fp).name.lower()

        # 1. Check files written THIS RUN first (highest confidence)
        for wp, content in self._written_files.items():
            if Path(wp).name.lower() == target_name:
                abs_path = self.workspace_path / wp
                if abs_path.exists():
                    return wp, content

        # 2. Direct path
        abs1 = self.workspace_path / fp
        if abs1.exists():
            try:
                return fp, abs1.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return fp, None

        # 3. Workspace scan
        import os as _os
        _SKIP = {"node_modules", "dist", ".git", "build", "target", "coverage", ".venv"}
        matches = []
        for root, dirs, files in _os.walk(str(self.workspace_path)):
            dirs[:] = [d for d in dirs if d not in _SKIP]
            for f in files:
                if f.lower() == target_name:
                    try:
                        rel = str(Path(root, f).relative_to(self.workspace_path)).replace("\\", "/")
                        matches.append(rel)
                    except ValueError:
                        pass

        if matches:
            # Prefer suffix match
            for m in matches:
                inner = m.split("/", 1)[-1] if "/" in m else m
                if fp.endswith(inner) or m.endswith(fp):
                    abs_m = self.workspace_path / m
                    try:
                        return m, abs_m.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        return m, None
            # First match
            abs_m = self.workspace_path / matches[0]
            try:
                return matches[0], abs_m.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return matches[0], None

        return fp, None

    def _resolve_fix_path(self, fp_raw: str) -> str:
        """Resolve LLM-returned fix path to canonical workspace-relative path.
        Priority: direct → written-this-run → workspace scan → suffix match → raw.
        """
        fp = fp_raw.replace("\\", "/")
        target_name = Path(fp).name.lower()

        # 1. Direct path
        if (self.workspace_path / fp).exists():
            return fp

        # 2. Written this run
        for wp in self._written_files:
            if Path(wp).name.lower() == target_name:
                if (self.workspace_path / wp).exists():
                    logger.info(f"  Resolved '{fp}' → '{wp}' (written this run)")
                    return wp

        # 3. Workspace scan
        import os as _os
        _SKIP = {"node_modules", "dist", ".git", "build", "target", "coverage", ".venv"}
        matches = []
        for root, dirs, files in _os.walk(str(self.workspace_path)):
            dirs[:] = [d for d in dirs if d not in _SKIP]
            for f in files:
                if f.lower() == target_name:
                    try:
                        rel = str(Path(root, f).relative_to(self.workspace_path)).replace("\\", "/")
                        matches.append(rel)
                    except ValueError:
                        pass

        if not matches:
            return fp  # truly new file — action-based validation will handle it

        if len(matches) == 1:
            logger.info(f"  Resolved '{fp}' → '{matches[0]}' (workspace scan)")
            return matches[0]

        # Suffix tie-breaker
        for m in matches:
            inner = m.split("/", 1)[-1] if "/" in m else m
            if fp.endswith(inner) or m.endswith(fp):
                logger.info(f"  Resolved '{fp}' → '{m}' (suffix match)")
                return m

        logger.warning(f"  Ambiguous '{fp}': {len(matches)} candidates — using first")
        return matches[0]

    def _get_lsp_context(self, file_path: str, current_content: Optional[str]) -> str:
        """
        Build LSP context block for the LLM prompt.
        For HTML files: injects the sibling .ts controller's declared members.
        For TS files: injects types of imported interfaces/models.
        """
        ext = Path(file_path).suffix.lower()
        blocks: list[str] = []

        if ext in (".html", ".htm"):
            # Get sibling .ts controller's declared members
            stem = file_path.rsplit(".", 1)[0]
            for ctrl_ext in (".ts", ".tsx"):
                ctrl_path = stem + ctrl_ext
                # First check session files (most up-to-date)
                ctrl_key = ctrl_path.replace("\\", "/").lower()
                ctrl_content = self._written_files.get(ctrl_key)
                if not ctrl_content:
                    ctrl_abs = self.workspace_path / ctrl_path
                    if ctrl_abs.exists():
                        try:
                            ctrl_content = ctrl_abs.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            pass
                if ctrl_content:
                    members = self.symbol_index._ts_lsp._extract_via_regex(
                        self.workspace_path / ctrl_path, ctrl_path
                    )
                    if members:
                        blocks.append(
                            f"\n=== SIBLING CONTROLLER MEMBERS (use ONLY these names in template) ===\n"
                            f"{members.to_prompt_block()}\n"
                        )
                    break

        elif ext in (".ts", ".tsx") and current_content:
            # For TS files, show session-generated related types
            import os as _os
            src_dir = _os.path.dirname(file_path.replace("\\", "/"))
            imp_re = re.compile(r"""from\s+['"](\.[^'"]+)['"]""")
            for m in imp_re.finditer(current_content):
                rel = m.group(1)
                for cand_ext in (".ts", ".tsx"):
                    cand = _os.path.normpath(_os.path.join(src_dir, rel + cand_ext)).replace("\\", "/").lower()
                    if cand in self._written_files:
                        blocks.append(
                            f"\n=== IMPORTED FILE (freshly modified this run) ===\n"
                            f"{cand}\n{self._written_files[cand][:500]}\n"
                        )
                        break

        return "\n".join(blocks) if blocks else ""

    def _build_session_context(self, current_file: str) -> str:
        """Brief summary of files written so far in this run."""
        if not self._written_files:
            return ""
        lines = ["\n=== FILES MODIFIED EARLIER IN THIS RUN ==="]
        for fp in sorted(self._written_files.keys()):
            if fp != current_file.replace("\\", "/").lower():
                lines.append(f"  ✅ {fp}")
        return "\n".join(lines) + "\n"

    def _summarize_written_files(self) -> str:
        """Short content summary of written files for agent decision."""
        parts = []
        for fp, content in list(self._written_files.items())[:6]:
            parts.append(f"--- {fp} ---\n{content[:300]}")
        return "\n\n".join(parts)

    def _smart_extract_for_large_file(
        self, content: str, edit_description: str, file_path: str
    ) -> str:
        """
        Smart file reading for large files — like how top AI IDEs work.
        
        Instead of dumb truncation, extract:
        1. FILE OUTLINE: all class/method signatures with line numbers
        2. RELEVANT SECTIONS: full content of methods matching the edit task
        3. IMPORTS + CLASS HEADER: always included for context
        
        This lets the LLM see the structure of a 112K file AND the exact
        methods it needs to edit, without wasting tokens on unrelated code.
        """
        lines = content.split('\n')
        total_lines = len(lines)
        content_len = len(content)
        
        # Determine file type
        fp_lower = file_path.lower()
        is_java = fp_lower.endswith('.java')
        is_ts = fp_lower.endswith(('.ts', '.tsx'))
        is_py = fp_lower.endswith('.py')
        is_html = fp_lower.endswith(('.html', '.htm'))
        
        # --- Step 1: Extract method/function boundaries ---
        method_boundaries = []  # [(start_line, end_line, signature)]
        
        if is_java or is_ts:
            # Match Java/TS method signatures
            import re as _re
            method_pattern = _re.compile(
                r'^\s*(?:public|private|protected|static|async|abstract|override|final|synchronized|\s)*'
                r'(?:[\w<>\[\],\s]+\s+)?'
                r'(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{',
                _re.MULTILINE
            )
            # Also match class declarations
            class_pattern = _re.compile(
                r'^\s*(?:export\s+)?(?:public|private|protected|abstract|final|\s)*'
                r'(?:class|interface|enum)\s+(\w+)',
                _re.MULTILINE
            )
            
            brace_depth = 0
            current_method_start = None
            current_sig = None
            
            for i, line in enumerate(lines):
                # Track class declarations
                cm = class_pattern.match(line)
                if cm:
                    method_boundaries.append((i, i, f"class {cm.group(1)}"))
                
                # Track method starts
                mm = method_pattern.match(line)
                if mm and brace_depth <= 1:  # Top-level or class-level method
                    current_method_start = i
                    current_sig = line.strip()[:120]
                
                # Track braces to find method end
                brace_depth += line.count('{') - line.count('}')
                
                if current_method_start is not None and brace_depth <= 1:
                    method_boundaries.append((current_method_start, i, current_sig))
                    current_method_start = None
                    current_sig = None
                    
        elif is_py:
            import re as _re
            func_pattern = _re.compile(r'^(\s*)(def|class)\s+(\w+)')
            current_start = None
            current_sig = None
            current_indent = 0
            
            for i, line in enumerate(lines):
                m = func_pattern.match(line)
                if m:
                    if current_start is not None:
                        method_boundaries.append((current_start, i - 1, current_sig))
                    current_start = i
                    current_sig = line.strip()[:120]
                    current_indent = len(m.group(1))
            if current_start is not None:
                method_boundaries.append((current_start, total_lines - 1, current_sig))
        
        # --- Step 2: Build outline ---
        outline_parts = []
        outline_parts.append(f"// FILE: {file_path} ({content_len:,} chars, {total_lines} lines)")
        outline_parts.append(f"// STRUCTURE OUTLINE ({len(method_boundaries)} methods/classes):")
        outline_parts.append("")
        
        for start, end, sig in method_boundaries:
            size = end - start + 1
            outline_parts.append(f"  L{start+1}-L{end+1} ({size} lines): {sig}")
        
        # --- Step 3: Find relevant methods ---
        edit_desc_lower = (edit_description or "").lower()
        # Extract keywords from edit description
        import re as _re
        keywords = set(_re.findall(r'[a-zA-Z]\w{3,}', edit_desc_lower))
        
        relevant_sections = []
        relevant_starts = set()  # Track by start line to avoid duplicates
        
        for start, end, sig in method_boundaries:
            sig_lower = sig.lower()
            # Check if method name or signature overlaps with edit keywords
            sig_words = set(_re.findall(r'[a-zA-Z]\w{3,}', sig_lower))
            overlap = keywords & sig_words
            
            if overlap:
                # This method is relevant — include full content
                section_lines = lines[start:end + 1]
                relevant_sections.append((start, end, sig, '\n'.join(section_lines)))
                relevant_starts.add(start)
        
        # If no keyword match found, try to find methods containing edit keywords in their body
        if not relevant_sections:
            for start, end, sig in method_boundaries:
                body = '\n'.join(lines[start:end + 1]).lower()
                body_overlap = sum(1 for kw in keywords if kw in body)
                if body_overlap >= 2:  # At least 2 keywords found in method body
                    section_lines = lines[start:end + 1]
                    relevant_sections.append((start, end, sig, '\n'.join(section_lines)))
                    relevant_starts.add(start)
                    if len(relevant_sections) >= 5:  # Cap at 5 relevant methods
                        break
        
        # --- Step 3b: Follow call chains within the same file ---
        # If method A calls method B (in the same file), include B too.
        # This is how top AI IDEs work — they follow the call graph.
        # Do 2 levels deep: A→B, B→C
        if relevant_sections and method_boundaries:
            # Build a lookup: method_name → (start, end, sig, body)
            method_by_name = {}
            for start, end, sig in method_boundaries:
                # Extract method name from signature
                name_match = _re.search(r'(\w+)\s*\(', sig)
                if name_match:
                    method_by_name[name_match.group(1)] = (start, end, sig)
            
            # Extract method calls from relevant methods' bodies
            for depth in range(2):  # 2 levels deep
                new_calls = set()
                for start, end, sig, body in relevant_sections:
                    # Find method calls: this.methodName(, methodName(, self.method_name(
                    calls = _re.findall(r'(?:this\.|self\.)?(\w+)\s*\(', body)
                    for call_name in calls:
                        if call_name in method_by_name:
                            called_start = method_by_name[call_name][0]
                            if called_start not in relevant_starts:
                                new_calls.add(call_name)
                
                if not new_calls:
                    break  # No new methods to add
                    
                for call_name in new_calls:
                    c_start, c_end, c_sig = method_by_name[call_name]
                    if c_start not in relevant_starts:
                        section_lines = lines[c_start:c_end + 1]
                        relevant_sections.append(
                            (c_start, c_end, f"[CALLED BY ABOVE] {c_sig}", '\n'.join(section_lines))
                        )
                        relevant_starts.add(c_start)
        
        # --- Step 4: Build the smart content ---
        parts = []
        
        # Always include imports + class header (first 80 lines or until first method)
        first_method_line = method_boundaries[0][0] if method_boundaries else 80
        header_end = min(first_method_line, 80)
        parts.append("// === IMPORTS & CLASS HEADER ===")
        parts.append('\n'.join(lines[:header_end]))
        
        # Include outline
        parts.append("")
        parts.append('\n'.join(outline_parts))
        
        # Include relevant methods in full
        if relevant_sections:
            parts.append("")
            parts.append(f"// === RELEVANT METHODS ({len(relevant_sections)} sections shown in FULL) ===")
            for start, end, sig, body in relevant_sections:
                parts.append(f"\n// --- L{start+1}-L{end+1}: {sig} ---")
                parts.append(body)
        else:
            # No specific methods matched — show first 30K + outline
            parts.append("")
            parts.append("// === FILE CONTENT (first 30K chars — use outline above for navigation) ===")
            parts.append(content[:30000])
        
        result = '\n'.join(parts)
        
        # Safety cap: if result is still huge, truncate with note
        if len(result) > 60000:
            result = result[:55000] + f"\n\n// [TRUNCATED at 55K chars — file is {content_len:,} total]"
        
        return result



    def _apply_search_replace(self, original: str, llm_response: str, is_create: bool) -> Optional[str]:
        """Apply additive insertion strategies or SEARCH/REPLACE from LLM response."""
        if is_create or not original:
            code_m = re.search(r'```[\w]*\n(.*?)```', llm_response, re.DOTALL)
            if code_m:
                return code_m.group(1)
            clean = re.sub(r'^```[\w]*\n|```$', '', llm_response.strip(), flags=re.MULTILINE)
            return clean if clean.strip() else None

        # ── Primary: Aider-style SEARCH/REPLACE blocks ─────────────────────
        from ticket_to_code.agents.code_generator import _apply_str_replace_edits
        aider_pattern = re.compile(
            r'<{7} SEARCH\n(.*?)\n={7}\n(.*?)\n>{7} REPLACE',
            re.DOTALL
        )
        aider_matches = list(aider_pattern.finditer(llm_response))
        if aider_matches:
            edits = [
                {"old_str": m.group(1), "new_str": m.group(2), "scope_method": ""}
                for m in aider_matches
            ]
            try:
                patched = _apply_str_replace_edits(edits, original, "edit_loop")
                logger.info(
                    f"  edit_loop: {len(edits)} Aider-style edit(s) applied"
                )
                return patched
            except ValueError as e:
                logger.warning(f"  Aider-style edit failed in edit_loop: {e}")

        # ── Fallback: legacy <<</>>> EDIT blocks ─────────────────────────
        legacy_pattern = re.compile(
            r'EDIT:\s*\n'
            r'old_str:\s*\n<<<\n(.*?)\n>>>\s*\n'
            r'new_str:\s*\n<<<\n(.*?)\n>>>',
            re.DOTALL
        )
        legacy_matches = list(legacy_pattern.finditer(llm_response))
        if legacy_matches:
            edits = [
                {"old_str": m.group(1), "new_str": m.group(2), "scope_method": ""}
                for m in legacy_matches
            ]
            try:
                patched = _apply_str_replace_edits(edits, original, "edit_loop")
                logger.info(
                    f"  edit_loop: {len(edits)} legacy edit(s) applied"
                )
                return patched
            except ValueError as e:
                logger.warning(f"  Legacy edit failed in edit_loop: {e}")

        # ── Fallback: <<<SEARCH>>>/<<<REPLACE>>>/<<<END>>> format ─────────
        result = original
        pattern = re.compile(
            r'<<<SEARCH>>>\n(.*?)<<<REPLACE>>>\n(.*?)<<<END>>>',
            re.DOTALL
        )
        applied = 0
        for m in pattern.finditer(llm_response):
            search_text = m.group(1)
            replace_text = m.group(2)
            if search_text in result:
                result = result.replace(search_text, replace_text, 1)
                applied += 1
            else:
                search_normalized = re.sub(r'[ \t]+', ' ', search_text.strip())
                result_normalized = re.sub(r'[ \t]+', ' ', result)
                if search_normalized in result_normalized:
                    idx = result_normalized.find(search_normalized)
                    result = result[:idx] + replace_text + result[idx + len(search_normalized):]
                    applied += 1

        if applied == 0:
            logger.warning(f"  No SEARCH/REPLACE blocks matched — edit not applied")
            return None
        return result

    def _verify_file(self, file_path: str) -> list[str]:
        """Run the appropriate compiler/syntax checker for the file type."""
        ext = Path(file_path).suffix.lower()
        if ext in (".ts", ".tsx") and not file_path.endswith(".spec.ts"):
            written_ts = [
                fp for fp in self._written_files.keys()
                if fp.endswith((".ts", ".tsx")) and not fp.endswith(".spec.ts")
            ]
            return self.symbol_index.get_ts_diagnostics([file_path] + written_ts)
        if ext in (".py",) and not file_path.endswith("_test.py"):
            return self.symbol_index.get_python_diagnostics([file_path])
        if ext in (".scss", ".css"):
            # Fast syntax check — balanced braces + format marker leak detection
            from ticket_to_code.workflow import _check_scss_syntax
            content = self._written_files.get(file_path.replace("\\", "/").lower(), "")
            if not content:
                abs_path = self.workspace_path / file_path
                if abs_path.exists():
                    content = abs_path.read_text(encoding="utf-8", errors="ignore")
            return _check_scss_syntax(content, file_path) if content else []
        if ext == ".html":
            from ticket_to_code.workflow import _check_html_syntax
            content = self._written_files.get(file_path.replace("\\", "/").lower(), "")
            if not content:
                abs_path = self.workspace_path / file_path
                if abs_path.exists():
                    content = abs_path.read_text(encoding="utf-8", errors="ignore")
            return _check_html_syntax(content, file_path) if content else []
        # Java and C# verification is handled by the full build phase
        return []

    def _read_file(self, file_path: str) -> Optional[str]:
        """Read file from disk, with session map fallback."""
        key = file_path.replace("\\", "/").lower()
        if key in self._written_files:
            return self._written_files[key]
        abs_path = self.workspace_path / file_path
        if abs_path.exists():
            try:
                return abs_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
        return None

    def _write_file(self, file_path: str, content: str) -> None:
        """Write file to disk and update session map."""
        abs_path = self.workspace_path / file_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        self._written_files[file_path.replace("\\", "/").lower()] = content
        logger.info(f"  📝 Written: {file_path}")
