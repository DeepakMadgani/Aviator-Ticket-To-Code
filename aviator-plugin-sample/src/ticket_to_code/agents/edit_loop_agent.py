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
    from ticket_to_code.agents.implementation_state import ImplementationState

from ticket_to_code.agents.lsp_client import WorkspaceSymbolIndex, ClassMembers
from ticket_to_code.agents.symbol_resolver import SymbolResolver

logger = logging.getLogger(__name__)


MAX_ITER = 8           # safety cap — never exceed this many file edits
MAX_FIX_PER_FILE = 3   # max inline fix attempts per file before moving on


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


@dataclass
class ApplyEditResult:
    """Structured result of applying search/replace edits to content."""
    success: bool
    content: str
    applied_count: int = 0
    failure_reason: str = ""
    failed_search: str = ""
    parser_format: str = ""  # "aider", "legacy", "delimiters", "create", "none"


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
        impl_state: Optional["ImplementationState"] = None,
        ui_callback=None,
    ):
        self.workspace_path = Path(workspace_path)
        self.llm = llm
        self.symbol_index = symbol_index or WorkspaceSymbolIndex(workspace_path)
        self._resolver = SymbolResolver(self.symbol_index)
        self._ng_root: Optional[Path] = None
        self._written_files: dict[str, str] = {}  # path → last written content
        self._impl_state = impl_state  # Four-pillar ImplementationState (optional)
        self.ui_callback = ui_callback

    @staticmethod
    def _get_live_check_label(file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        if ext in (".html", ".htm"):
            return "Template-Check"
        elif ext in (".ts", ".tsx"):
            return "TSC-live"
        elif ext in (".java", ".kt", ".scala"):
            return "Java-Check"
        elif ext in (".scss", ".css", ".sass", ".less"):
            return "Style-Check"
        return "Build-Check"

    def _ui_emit(self, event_type: str, **kwargs):
        if not self.ui_callback:
            return
        from datetime import datetime
        self.ui_callback({
            "phase": "patch_generation",
            "status": "in_progress",
            "message": kwargs.get("message", ""),
            "data": {
                "node": "generate_code",
                "event_type": event_type,
                **{k: v for k, v in kwargs.items() if k != "message"},
            },
            "timestamp": datetime.now().isoformat(),
        })

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

        files_edited: set[str] = set()
        if existing_file_map:
            self._written_files.update(existing_file_map)
            files_edited.update(existing_file_map.keys())

        edit_results: list[EditResult] = []
        iteration = 0
        _seen_error_sigs: set[str] = set()   # bounded safety net (repeated-error stop)

        # Seed the initial file queue from the architectural plan (if provided)
        # Files already written and compiling clean are skipped.
        pending_queue: list[EditDecision] = []
        norm_existing = {k.replace("\\", "/").lower() for k in self._written_files}
        if initial_plan and initial_plan.tasks:
            for t in initial_plan.tasks:
                if getattr(getattr(t, "task_type", None), "value", "") != "read_only":
                    norm_fp = t.file_path.replace("\\", "/").lower()
                    if norm_fp in norm_existing:
                        errs = self._verify_file(t.file_path)
                        if not errs:
                            logger.info(f"  ⏭️ File {t.file_path} already generated and compiles clean — skipping re-queue")
                            edit_results.append(EditResult(
                                file_path=t.file_path,
                                content_before="",
                                content_after=self._written_files.get(t.file_path) or self._written_files.get(norm_fp) or "",
                                compile_clean=True,
                                skipped=False,
                                reason="already generated and clean",
                            ))
                            continue
                    pending_queue.append(EditDecision(
                        file_path=t.file_path,
                        reason=f"Planner task: {t.title}",
                        edit_description=t.description or t.title,
                        is_new_file=getattr(getattr(t, "task_type", None), "value", "") == "create",
                        priority=3,
                    ))

        # Fast 0-call early exit: if plan tasks were provided, all exist in written files,
        # and pending_queue is empty with all files clean.
        if initial_plan and initial_plan.tasks and not pending_queue and edit_results:
            if all(r.compile_clean for r in edit_results):
                logger.info(
                    f"  ⏹️ Edit loop fast-exit: all {len(edit_results)} planned file(s) "
                    "already generated and compile clean (0 calls burned)."
                )
                return {
                    "generated_files": dict(self._written_files),
                    "edit_results": edit_results,
                    "final_compile_errors": [],
                    "iterations": 0,
                    "solved": True,
                }

        while iteration < MAX_ITER:
            # ── Early exit: nothing left to fix ───────────────────────────────
            if iteration >= 1 and not pending_queue:
                latest_by_file: dict[str, EditResult] = {}
                for r in edit_results:
                    latest_by_file[r.file_path.replace("\\", "/").lower()] = r
                _dirty = [
                    r for r in latest_by_file.values()
                    if (not r.skipped and not r.compile_clean)
                    or (r.skipped and r.reason.startswith("edit application failed"))
                ]
                if not _dirty:
                    logger.info(
                        "  ⏹️ Edit loop early-exit: queue empty and all edited "
                        f"files clean after {iteration} iteration(s)."
                    )
                    break

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
                reason = "no change generated" if new_content == current_content else "edit application failed"
                logger.warning(f"  {reason.capitalize()} for {decision.file_path} — skipping write")
                edit_results.append(EditResult(
                    file_path=decision.file_path,
                    content_before=current_content or "",
                    content_after=current_content or "",
                    skipped=True,
                    reason=reason,
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

                # ── Cross-file error detection (compile-driven) ──────────
                # When errors reference a type/class defined in a DIFFERENT
                # file (e.g. "Property 'X' does not exist on type 'Y'"),
                # queue that file for proper editing in the next iteration.
                # This is how Cursor/Copilot handle cross-file dependencies:
                # reactively from compiler output, not pre-emptive scanning.
                cross_file_targets = self._extract_cross_file_targets(
                    compile_errors, decision.file_path
                )

                if cross_file_targets:
                    for target_path, missing_members in cross_file_targets.items():
                        if target_path not in files_edited and target_path not in {
                            q.file_path for q in pending_queue
                        }:
                            pending_queue.insert(0, EditDecision(
                                file_path=target_path,
                                reason=(
                                    f"Compiler error: {Path(decision.file_path).name} "
                                    f"references members not found in "
                                    f"{Path(target_path).name}: "
                                    f"{', '.join(missing_members)}"
                                ),
                                edit_description=(
                                    f"Add the following members to match usage in "
                                    f"{Path(decision.file_path).name}: "
                                    f"{', '.join(missing_members)}"
                                ),
                                is_new_file=False,
                                priority=1,  # High priority — unblock dependent
                            ))
                            logger.info(
                                f"  🔗 Cross-file error → queued {target_path} for "
                                f"missing members: {missing_members}"
                            )

                    # Filter out cross-file errors — only try inline fix for
                    # same-file errors (the cross-file ones will be fixed when
                    # the queued file is properly edited next iteration)
                    same_file_errors = [
                        e for e in compile_errors
                        if not self._is_cross_file_error(e, decision.file_path)
                    ]
                    compile_errors = same_file_errors

                # ── Same-file inline fix (original logic) ────────────────
                if compile_errors:
                    for fix_attempt in range(MAX_FIX_PER_FILE):
                        logger.info(f"  Inline fix attempt {fix_attempt + 1}/{MAX_FIX_PER_FILE}")
                        # Snapshot written files before fix to detect cross-file changes
                        _pre_fix_snapshot = dict(self._written_files)
                        fixed = self._fix_errors(
                            decision.file_path, new_content, compile_errors,
                            ticket, requirements, code_rag_context, lsp_context
                        )
                        if fixed and fixed != new_content:
                            # Primary file was modified by the fix
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
                            # Primary file wasn't changed — but cross-file fixes
                            # may have been applied (e.g. adding a method to a
                            # service file that the primary file imports).
                            # Detect this by comparing _written_files snapshots.
                            _cross_file_changed = {
                                k for k, v in self._written_files.items()
                                if k not in _pre_fix_snapshot or _pre_fix_snapshot[k] != v
                            }
                            if _cross_file_changed:
                                logger.info(
                                    f"  🔗 Cross-file fix applied to "
                                    f"{len(_cross_file_changed)} file(s): "
                                    f"{[Path(p).name for p in _cross_file_changed]}"
                                    f" — re-verifying primary file"
                                )
                                fix_applied = True
                                remaining_errors = self._verify_file(decision.file_path)
                                if not remaining_errors:
                                    logger.info(f"  ✅ Cross-file fix resolved all errors in {Path(decision.file_path).name}")
                                    compile_errors = []
                                    break
                                compile_errors = remaining_errors
                                # Don't break — try another inline fix attempt
                                # if errors remain and we have attempts left
                            else:
                                logger.info(f"  Fix attempt produced no change")
                                break

            # Bounded safety net: stop if the SAME unresolved error set recurs,
            # instead of consuming the remaining iterations on the same fix.
            if compile_errors:
                from ticket_to_code.agents.edit_loop_policy import error_signature as _esig
                _sig = _esig(compile_errors)
                if _sig in _seen_error_sigs:
                    logger.warning(
                        f"  ⏹️ Edit loop stop: identical unresolved errors recurred for "
                        f"{decision.file_path} — returning failure instead of repeating the same fix."
                    )
                    edit_results.append(EditResult(
                        file_path=decision.file_path,
                        content_before=current_content or "",
                        content_after=new_content,
                        compile_errors=compile_errors,
                        compile_clean=False,
                    ))
                    break
                _seen_error_sigs.add(_sig)

            compile_clean = not bool(compile_errors)
            file_basename = Path(decision.file_path).name
            check_label = self._get_live_check_label(decision.file_path)

            if compile_clean:
                logger.info(f"  ✅ {decision.file_path} — clean compile [{check_label}]")
                self._ui_emit(
                    "live_check_passed",
                    message=f"✅ [{check_label}] Compiled OK — {file_basename}",
                    file_path=decision.file_path,
                    file_name=file_basename,
                    check_type=check_label,
                )
            else:
                logger.warning(
                    f"  ⚠️ {decision.file_path} — {len(compile_errors)} error(s) remain "
                    f"(will be addressed by full build phase)"
                )
                self._ui_emit(
                    "live_check_start",
                    message=f"⚠️ [{check_label}] {len(compile_errors)} compile error(s) in {file_basename}",
                    file_path=decision.file_path,
                    file_name=file_basename,
                    error_count=len(compile_errors),
                    errors=compile_errors[:5],
                    check_type=check_label,
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
        has_failed_edits = any(r.reason.startswith("edit application failed") for r in edit_results)
        solved = not bool(final_errors) and not has_failed_edits and bool(files_edited)
        if solved:
            logger.info(f"\n✅ EDIT LOOP COMPLETE: Ticket solved in {iteration} iteration(s)")
        else:
            logger.warning(
                f"\n⚠️ EDIT LOOP COMPLETE: Ticket not solved ({len(final_errors)} compile error(s), "
                f"failed_edits={has_failed_edits}) after {iteration} iteration(s)"
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

        # All planned files done — check if all edited files compile cleanly
        if not files_edited:
            return None

        all_clean = True
        for fp in files_edited:
            errs = self._verify_file(fp)
            if errs:
                all_clean = False
                break
        if all_clean:
            logger.info("  ⏹️ All planned files edited and compile cleanly — ticket complete (0 extra LLM calls).")
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
5. Patches must be strictly minimal and proportional to the target symbol. Whole-file rewrites will be rejected.
6. Do NOT output conversational preamble, greetings, or long reasoning. Begin immediately with the SUMMARY line.

{"ANGULAR TEMPLATE RULE: Before writing any *ngIf or {{ }} binding, read the SIBLING CONTROLLER section in the context. Use ONLY property names that are DECLARED in that controller. Never invent new names." if is_html else ""}

OUTPUT FORMAT:
SUMMARY:
<one concise user-facing sentence explaining what this edit changes>

{"```" + (ext[1:] if ext else "text") + "\n<complete new content>\n```" if is_create else """<<<<<<< SEARCH
<exact existing code to find (must match EXACTLY ONCE in the file)>
=======
<replacement code>
>>>>>>> REPLACE"""}

SEARCH/REPLACE RULES:
- The SEARCH block must be an EXACT substring of the CURRENT FILE CONTENT (or EXACT SOURCE section) shown below — copy it character-for-character including indentation.
- NEVER copy lines containing comment headers, outlines, or summaries.
- The SEARCH block must match EXACTLY ONCE in the file. If it could match multiple places, include more surrounding lines to make it unique.
- Prefer multiple small SEARCH/REPLACE blocks over one very large block.
- NEVER put the entire file content in a SEARCH block.
- For ADDING new code: use a small anchor from existing code as SEARCH, and include anchor + new code as REPLACE.
- You may use multiple SEARCH/REPLACE blocks in a single response."""

        # Target-first file content: verbatim source for small files, skeleton+exact for large files
        if current_content:
            from ticket_to_code.agents.smart_extract import smart_extract, extract_exact_methods
            
            # Extract target method/symbol from decision description or reason
            target_symbol = None
            sym_match = re.search(
                r'\b(?:method|function|property|class)\s+[`\'"]?([a-zA-Z_$][a-zA-Z0-9_$]*)',
                f"{decision.reason} {decision.edit_description}",
                re.IGNORECASE
            )
            if sym_match:
                target_symbol = sym_match.group(1)

            target_methods = [target_symbol] if target_symbol else []
            line_count = len(current_content.splitlines())
            char_count = len(current_content)

            # If the file is normal sized (<= 600 lines or <= 30k chars), pass pure VERBATIM source
            # This completely avoids synthetic outline comments (// === FILE OUTLINE) poisoning SEARCH blocks!
            if line_count <= 600 or char_count <= 30000:
                file_content_block = current_content
            else:
                verbatim_content, matched_boundaries = extract_exact_methods(
                    content=current_content,
                    file_path=decision.file_path,
                    anchor_methods=target_methods,
                )
                if matched_boundaries:
                    skeleton = smart_extract(
                        current_content,
                        file_path=decision.file_path,
                        allowed_methods=target_methods,
                        target_method=target_symbol,
                        edit_description=decision.edit_description,
                    )
                    file_content_block = (
                        "=== FILE STRUCTURE (context only — DO NOT copy text from this section) ===\n"
                        f"{skeleton}\n\n"
                        "=== EXACT SOURCE (your SEARCH block MUST be a verbatim substring of THIS section) ===\n"
                        f"{verbatim_content}"
                    )
                else:
                    # Fallback: verbatim window
                    file_content_block = current_content[:25000]
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

            # 1. Parse SUMMARY line for UI/user visibility
            summary_m = re.search(r'SUMMARY:\s*(.+?)(?:\n\n|<<<<<<<|\Z)', response.content, re.DOTALL)
            if summary_m:
                summary_line = summary_m.group(1).strip().splitlines()[0]
                logger.info(f"  edit_loop SUMMARY: {summary_line}")
                self._ui_emit(
                    "patch_summary",
                    message=f"✓ {summary_line}",
                    file_path=decision.file_path,
                )

            # 2. Validate patch proportionality via PatchGate before applying
            from ticket_to_code.agents.patch_gate import PatchGate
            prop_ok, prop_reason = PatchGate.validate_patch_proportionality(
                decision.file_path,
                response.content,
                current_content,
                is_new_file=is_create,
            )
            if not prop_ok:
                logger.warning(f"  ⛔ Patch rejected by PatchGate proportionality check: {prop_reason}")
                return None

            edit_res = self._apply_search_replace(current_content or "", response.content, decision.is_new_file)
            if edit_res.success:
                logger.info(
                    f"  edit_loop: {edit_res.applied_count} edit(s) applied to "
                    f"{decision.file_path} via [{edit_res.parser_format}]"
                )
                return edit_res.content

            # If create action or no existing content, fail safely
            if is_create or not current_content:
                logger.warning(f"  Create edit failed for {decision.file_path}: {edit_res.failure_reason}")
                return None

            # Edit failed — perform EXACTLY ONE retry with feedback (Phase 4)
            # Re-read fresh source from disk/storage to resolve drift
            fresh_content = self._read_file(decision.file_path) or current_content
            logger.warning(
                f"  Edit application failed for {decision.file_path}: {edit_res.failure_reason} "
                f"[{edit_res.parser_format}]. Re-reading fresh source and re-localizing AST boundary for single retry..."
            )

            # Re-localize containing method boundary on fresh source
            method_ctx = ""
            try:
                from ticket_to_code.agents.smart_extract import _get_reliable_boundaries
                fresh_lines = fresh_content.splitlines()
                boundaries = _get_reliable_boundaries(fresh_content, decision.file_path, fresh_lines)
                if boundaries and edit_res.failed_search:
                    search_first_line = edit_res.failed_search.strip().splitlines()[0].strip()
                    for idx, fl in enumerate(fresh_lines):
                        if search_first_line in fl:
                            for mb in boundaries:
                                if mb.kind != "class" and mb.start_line <= idx <= mb.end_line:
                                    method_ctx = "\n".join(fresh_lines[mb.start_line : mb.end_line + 1])
                                    break
                            if method_ctx:
                                break
            except Exception:
                pass

            feedback_lines = [
                "⚠️ YOUR PREVIOUS EDIT ATTEMPT FAILED TO APPLY:",
                f"Failure reason: {edit_res.failure_reason}",
            ]
            if edit_res.failed_search:
                feedback_lines.append(f"Failed SEARCH block:\n```\n{edit_res.failed_search}\n```")
            if method_ctx:
                feedback_lines.append(f"Re-localized containing method from fresh file:\n```\n{method_ctx}\n```")
            elif edit_res.failed_search:
                local_ctx = self._find_surrounding_context(fresh_content, edit_res.failed_search)
                if local_ctx:
                    feedback_lines.append(f"Surrounding context from current file:\n```\n{local_ctx}\n```")

            feedback_lines.extend([
                "",
                "CRITICAL INSTRUCTIONS FOR RETRY:",
                "1. Your previous SEARCH block does not match the current file.",
                "2. Do NOT regenerate the file.",
                "3. Return ONLY a corrected edit using the required format:",
                "   <<<<<<< SEARCH",
                "   <exact existing code copied character-for-character>",
                "   =======",
                "   <replacement code>",
                "   >>>>>>> REPLACE",
                "4. The SEARCH text must be copied character-for-character from the current file, including indentation.",
            ])
            retry_prompt = "\n".join(feedback_lines)

            from langchain_core.messages import AIMessage
            retry_response = llm_invoke(self.llm, [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
                AIMessage(content=response.content),
                HumanMessage(content=retry_prompt),
            ])

            retry_res = self._apply_search_replace(fresh_content, retry_response.content, False)
            if retry_res.success:
                logger.info(
                    f"  ✅ Retry succeeded for {decision.file_path}: {retry_res.applied_count} "
                    f"edit(s) applied via [{retry_res.parser_format}]"
                )
                return retry_res.content
            else:
                logger.warning(
                    f"  ❌ Retry also failed for {decision.file_path}: {retry_res.failure_reason}. "
                    f"Rejecting edit safely — existing code remains untouched."
                )
                return None
        except Exception as exc:
            logger.warning(f"  Edit generation failed for {decision.file_path}: {exc}")
            return None

    @staticmethod
    def _find_surrounding_context(content: str, failed_search: str) -> str:
        """Find surrounding lines from content for context without guessing or inventing a location."""
        if not content or not failed_search:
            return ""
        lines = [l.strip() for l in failed_search.splitlines() if len(l.strip()) >= 10]
        for candidate_line in sorted(lines, key=len, reverse=True):
            if content.count(candidate_line) == 1:
                content_lines = content.splitlines()
                for idx, cl in enumerate(content_lines):
                    if candidate_line in cl:
                        start = max(0, idx - 4)
                        end = min(len(content_lines), idx + 8)
                        return "\n".join(content_lines[start:end])
        return ""

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
        from ticket_to_code.agents.diagnostic_normalizer import normalize_diagnostics
        from ticket_to_code.agents.diagnostic_localizer import (
            DiagnosticLocalizer,
            RepairContextTier,
            FailureOwner,
        )

        localizer = getattr(self, "_diagnostic_localizer", None)
        if not localizer:
            localizer = DiagnosticLocalizer(self.workspace_path)
            self._diagnostic_localizer = localizer

        norm_diags = normalize_diagnostics(errors)
        if not localizer.can_attempt_repair(norm_diags, max_attempts=2):
            logger.warning(
                "  🛑 Bounded repair budget reached: identical diagnostics unchanged after 2 attempts in EditLoop."
            )
            return None
        localizer.record_attempt(norm_diags)

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
            '    {\n'
            '      "file": "exact/relative/path.ts",\n'
            '      "action": "modify",\n'
            '      "edits": [\n'
            '        {"search": "exact existing code to find", "replace": "replacement code"}\n'
            '      ]\n'
            '    },\n'
            '    {"file": "new/file/if/needed.ts", "action": "create", "content": "complete new file"}\n'
            "  ]\n"
            "}\n"
            "Rules:\n"
            "- For 'modify' actions: use 'edits' array with search/replace pairs (NOT complete file content)\n"
            "- The 'search' value must be an EXACT substring of the existing file — copy it character-for-character\n"
            "- The 'replace' value is what replaces the search text\n"
            "- Use multiple small edits rather than one giant edit\n"
            "- For 'create' actions ONLY: use 'content' with the complete new file\n"
            "- NEVER put complete file content in 'edits' — only the lines that change + minimal surrounding context\n"
            "- Fix every file that needs to change (primary + root-cause files)\n"
            "- Respond with JSON only, no markdown"
        )

        # ── Feedback loop: ask LLM → validate → rejected? → tell LLM why → retry ──
        MAX_FIX_ATTEMPTS = 3
        rejection_history: list[str] = []  # accumulates across attempts
        primary_content = None

        for attempt in range(MAX_FIX_ATTEMPTS):
            tier = (
                RepairContextTier.TIER_1_LOCALIZED_METHOD
                if attempt == 0
                else RepairContextTier.TIER_4_WHOLE_FILE_ESCALATION
            )
            loc_ctx = localizer.localize_context(file_path, current_content, norm_diags, tier=tier)
            if loc_ctx.context_tier == RepairContextTier.TIER_1_LOCALIZED_METHOD:
                primary_file_block = (
                    f"PRIMARY FILE (Tier 1 Localized Context around `{loc_ctx.target_method_name}`): {file_path}\n"
                    f"```\n{loc_ctx.prompt_snippet}\n```\n"
                )
            else:
                primary_file_block = (
                    f"PRIMARY FILE (Tier 4 Whole File Context): {file_path}\n"
                    f"```\n{current_content}\n```\n"
                )

            user_prompt = (
                f"TICKET: {ticket_title}\n{ticket_desc}\n\n"
                + (f"FUNCTIONAL REQUIREMENTS (must still be satisfied after fix):\n{func_reqs}\n\n" if func_reqs else "")
                + f"COMPILE ERRORS:\n{error_text}\n\n"
                + f"{lsp_context}\n\n"
                + primary_file_block
                + other_files_section
                + "\nFix all errors without removing any new functionality. Return JSON."
            )

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
                usage = getattr(response, "usage_metadata", {}) or {}
                tok = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
                if not tok:
                    tok = len(final_user_prompt.split()) + len(fix_raw.split())
                localizer.telemetry.edit_loop_tokens += tok
                localizer.telemetry.total_tokens += tok

                # Parse JSON response
                json_m = re.search(r'\{.*\}', fix_raw, re.DOTALL)
                if not json_m:
                    logger.warning(f"  Attempt {attempt+1}: No JSON in response — falling back to SEARCH/REPLACE")
                    edit_res = self._apply_search_replace(current_content, fix_raw, False)
                    if edit_res.success:
                        return edit_res.content
                    logger.warning(f"  SEARCH/REPLACE fallback failed: {edit_res.failure_reason}")
                    if attempt < MAX_FIX_ATTEMPTS - 1:
                        rejection_history.append(
                            f"Response was not valid JSON and SEARCH/REPLACE failed: {edit_res.failure_reason}. "
                            f"Return a valid JSON object with 'fixes' array."
                        )
                        continue
                    return None

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
                    fix_action = fix_item.get("action", "modify").lower().strip()
                    fix_edits = fix_item.get("edits", [])    # NEW: search/replace pairs
                    fix_content = fix_item.get("content", "")  # Only for CREATE actions

                    if not fix_path_raw:
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
                            fix_action = "modify"
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

                    # ── All validations passed — apply the fix ──
                    fix_abs.parent.mkdir(parents=True, exist_ok=True)

                    if fix_action == "create":
                        # CREATE: write complete content directly (correct for new files)
                        if not fix_content:
                            msg = f"action='create' for '{resolved_path}' but no 'content' provided."
                            logger.warning(f"  ❌ {msg}")
                            attempt_rejections.append(msg)
                            continue
                        fix_abs.write_text(fix_content, encoding="utf-8")
                        self._written_files[resolved_path.replace("\\", "/").lower()] = fix_content
                        logger.info(
                            f"    ✏️  CREATE: {resolved_path} "
                            f"({len(fix_content)} chars)"
                        )
                        applied_count += 1
                        if is_primary:
                            primary_content = fix_content

                    elif fix_edits:
                        # MODIFY with search/replace edits — route through the SAME safe atomic engine
                        existing_fix_content = (
                            current_content if is_primary
                            else self._read_file(resolved_path) or ""
                        )
                        canonical_edits = [
                            {"old_str": ep.get("search", ""), "new_str": ep.get("replace", "")}
                            for ep in fix_edits
                            if ep.get("search")
                        ]
                        if not canonical_edits:
                            msg = f"No valid search/replace pairs found in 'edits' for '{resolved_path}'"
                            logger.warning(f"  ❌ {msg}")
                            attempt_rejections.append(msg)
                            continue

                        edit_res = self._safe_match_and_replace(
                            original=existing_fix_content,
                            edits=canonical_edits,
                            parser_format="json_fix",
                        )
                        if edit_res.success:
                            fix_abs.write_text(edit_res.content, encoding="utf-8")
                            self._written_files[resolved_path.replace("\\", "/").lower()] = edit_res.content
                            logger.info(
                                f"    ✏️  MODIFY (S/R atomic): {resolved_path} "
                                f"({edit_res.applied_count} edit(s) applied)"
                            )
                            applied_count += 1
                            if is_primary:
                                primary_content = edit_res.content
                        else:
                            ast_ctx_hint = ""
                            try:
                                from ticket_to_code.agents.smart_extract import _get_reliable_boundaries
                                fresh_content = self._read_file(resolved_path) or existing_fix_content
                                fresh_lines = fresh_content.splitlines()
                                bounds = _get_reliable_boundaries(fresh_content, resolved_path, fresh_lines)
                                if bounds and edit_res.failed_search:
                                    s_first = edit_res.failed_search.strip().splitlines()[0].strip()
                                    for idx, fl in enumerate(fresh_lines):
                                        if s_first in fl:
                                            for mb in bounds:
                                                if mb.kind != "class" and mb.start_line <= idx <= mb.end_line:
                                                    ast_ctx_hint = (
                                                        f"\nFresh source for containing method `{mb.name}`:\n```\n"
                                                        + "\n".join(fresh_lines[mb.start_line : mb.end_line + 1])
                                                        + "\n```"
                                                    )
                                                    break
                                            if ast_ctx_hint:
                                                break
                            except Exception:
                                pass

                            msg = (
                                f"Atomic edit failed for '{resolved_path}': {edit_res.failure_reason}. "
                                f"Existing file remains untouched.{ast_ctx_hint}"
                            )
                            logger.warning(f"  ❌ {msg}")
                            attempt_rejections.append(msg)

                    elif fix_content:
                        # Reject full-file content for modify actions (Phase 6 & 7)
                        msg = (
                            f"action='modify' for '{resolved_path}' requires explicit search/replace edits. "
                            f"Full-file content was returned instead of 'edits' array. "
                            f"Use 'edits': [{{'search': '...', 'replace': '...'}}]."
                        )
                        logger.warning(f"  ❌ {msg}")
                        attempt_rejections.append(msg)

                    else:
                        msg = f"No 'edits' or 'content' provided for '{resolved_path}'"
                        logger.warning(f"  ❌ {msg}")
                        attempt_rejections.append(msg)

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
                SystemMessage(content=(
                    "Fix compile errors using SEARCH/REPLACE blocks. Fix ONLY what the error requires.\n"
                    "DELIMITER FORMAT:\n"
                    "<<<<<<< SEARCH\n"
                    "<exact existing code to find>\n"
                    "=======\n"
                    "<replacement code>\n"
                    ">>>>>>> REPLACE"
                )),
                HumanMessage(content=f"ERRORS:\n{error_text}\n\nFILE:\n```\n{current_content}\n```"),
            ])
            edit_res = self._apply_search_replace(current_content, response.content, False)
            return edit_res.content if edit_res.success else primary_content
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

                    # ── Include FULL controller source so the LLM can see ──
                    # exact property names used in method bodies (e.g.
                    # `existingOrganizationName` inside `onMemberAdd()`).
                    # Without this, the LLM only sees member signatures like
                    # `member: Member` and guesses property sub-names.
                    ctrl_preview = ctrl_content[:8000]
                    if len(ctrl_content) > 8000:
                        ctrl_preview += "\n// ... (truncated)"
                    blocks.append(
                        f"\n=== SIBLING CONTROLLER SOURCE (use exact property names from this code) ===\n"
                        f"```typescript\n{ctrl_preview}\n```\n"
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
        """Summary of files written so far in this run, WITH exported symbols.

        Previously this only showed file paths (e.g. '✅ fp') which told the
        LLM nothing about what was actually created. Now we extract exported
        symbols (classes, interfaces, methods) from the written content so the
        LLM generating file N knows exactly what file 1..N-1 exported.
        """
        if not self._written_files:
            return ""

        lines = ["\n=== FILES MODIFIED EARLIER IN THIS RUN ==="]
        _current_norm = current_file.replace("\\", "/").lower()

        for fp in sorted(self._written_files.keys()):
            if fp == _current_norm:
                continue
            content = self._written_files[fp]
            lines.append(f"  ✅ {fp}")

            # Extract exported symbols from the file content
            if content:
                _exports = self._extract_exported_symbols(content, fp)
                if _exports:
                    for _exp in _exports[:15]:  # cap per file
                        lines.append(f"       ↳ {_exp}")

        # Enrich with blueprint status from ImplementationState
        if self._impl_state and self._impl_state.signature_blueprints:
            lines.append("\n=== CROSS-FILE BLUEPRINT STATUS ===")
            for bp in self._impl_state.signature_blueprints[:15]:
                status = bp.status.value if bp.status else "unknown"
                owner = f"{bp.owner_class}." if bp.owner_class else ""
                sig_display = f" → {bp.signature}" if bp.signature else ""
                lines.append(
                    f"  [{status.upper():11}] {owner}{bp.symbol_name}{sig_display} "
                    f"in {Path(bp.file_path).name if bp.file_path else '?'}"
                )

        return "\n".join(lines) + "\n"

    @staticmethod
    def _extract_exported_symbols(content: str, file_path: str) -> list[str]:
        """Extract human-readable exported symbol declarations from file content.

        Returns lines like:
          'export class AddMembersComponent { ... }'
          'export interface ProjectMemberCheckResponse { isMember: boolean; ... }'
          'public checkProjectMembership(projectId: string): Observable<...>'
        """
        symbols: list[str] = []
        ext = Path(file_path).suffix.lower()

        if ext in (".ts", ".tsx", ".js", ".jsx"):
            # TypeScript/JS: exported classes, interfaces, functions, types
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("export ") and any(
                    kw in stripped for kw in ("class ", "interface ", "enum ", "type ", "function ", "const ")
                ):
                    # Trim to signature only (first 120 chars)
                    symbols.append(stripped[:120])
                elif stripped.startswith(("public ", "private ", "protected ")) and "(" in stripped:
                    # Class method declaration
                    sig = stripped.split("{")[0].strip().rstrip(":")
                    if len(sig) > 10:
                        symbols.append(sig[:120])
        elif ext == ".java":
            # Java: public class/interface declarations and public methods
            for line in content.splitlines():
                stripped = line.strip()
                if any(kw in stripped for kw in ("public class ", "public interface ", "public enum ")):
                    symbols.append(stripped.split("{")[0].strip()[:120])
                elif stripped.startswith("public ") and "(" in stripped:
                    sig = stripped.split("{")[0].strip()
                    if len(sig) > 10:
                        symbols.append(sig[:120])
        elif ext == ".py":
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("class ") or stripped.startswith("def "):
                    symbols.append(stripped.rstrip(":").strip()[:120])

        return symbols


    def _summarize_written_files(self) -> str:
        """Structural summary of written files for agent decision."""
        parts = []
        for fp, content in list(self._written_files.items())[:6]:
            symbols = self._extract_exported_symbols(content, fp)
            if symbols:
                sym_str = "\n".join(f"    - {s}" for s in symbols[:8])
                parts.append(f"--- {fp} ({len(content)} chars) ---\nDeclared Symbols / Methods:\n{sym_str}")
            else:
                parts.append(f"--- {fp} ({len(content)} chars) ---\n{content[:400]}")
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



    def _safe_match_and_replace(
        self, original: str, edits: list[dict], parser_format: str
    ) -> ApplyEditResult:
        """Apply a batch of edits atomically using safe matching only.

        Matching order:
          1. Exact match (count == 1)
          2. CRLF/LF line-ending normalization (count == 1)
          3. Whitespace-normalized sliding window (count == 1)

        If any edit produces 0 matches or >1 ambiguous matches, the entire batch
        fails safely and ZERO changes are applied to original content.
        NO fuzzy matching or SequenceMatcher is ever used.
        """
        result = original
        applied_count = 0

        for i, edit in enumerate(edits):
            old_str = edit.get("old_str", "")
            new_str = edit.get("new_str", "")

            if not old_str:
                return ApplyEditResult(
                    success=False,
                    content=original,
                    failure_reason=f"Edit {i+1}: SEARCH block is empty",
                    failed_search="",
                    parser_format=parser_format,
                )

            # ── 1. Exact match ──────────────────────────────────────────
            count = result.count(old_str)
            if count == 1:
                result = result.replace(old_str, new_str, 1)
                applied_count += 1
                logger.info(f"  edit_loop edit {i+1}/{len(edits)} applied: Exact match")
                continue
            if count > 1:
                logger.warning(
                    f"  edit_loop edit {i+1}/{len(edits)} ambiguous: matches {count} locations"
                )
                return ApplyEditResult(
                    success=False,
                    content=original,
                    failure_reason=f"SEARCH block matched multiple locations ({count} matches, ambiguous)",
                    failed_search=old_str,
                    parser_format=parser_format,
                )

            # ── 2. CRLF/LF line-ending adaptation ───────────────────────
            adapted = None
            if "\n" in old_str and "\r\n" not in old_str:
                cand = old_str.replace("\n", "\r\n")
                c = result.count(cand)
                if c == 1:
                    adapted = cand
                elif c > 1:
                    return ApplyEditResult(
                        success=False,
                        content=original,
                        failure_reason=f"SEARCH block matched multiple locations after CRLF adaptation ({c} matches, ambiguous)",
                        failed_search=old_str,
                        parser_format=parser_format,
                    )
            elif "\r\n" in old_str:
                cand = old_str.replace("\r\n", "\n")
                c = result.count(cand)
                if c == 1:
                    adapted = cand
                elif c > 1:
                    return ApplyEditResult(
                        success=False,
                        content=original,
                        failure_reason=f"SEARCH block matched multiple locations after CRLF adaptation ({c} matches, ambiguous)",
                        failed_search=old_str,
                        parser_format=parser_format,
                    )

            if adapted is not None:
                result = result.replace(adapted, new_str, 1)
                applied_count += 1
                logger.info(f"  edit_loop edit {i+1}/{len(edits)} applied: CRLF-adapted")
                continue

            # ── 3. Whitespace-normalized sliding window ─────────────────
            search_lines = old_str.strip().splitlines()
            if not search_lines:
                return ApplyEditResult(
                    success=False,
                    content=original,
                    failure_reason=f"Edit {i+1}: SEARCH block contains only whitespace",
                    failed_search=old_str,
                    parser_format=parser_format,
                )

            normalized_search = [re.sub(r'\s+', ' ', l.strip()) for l in search_lines]
            if all(not s for s in normalized_search):
                return ApplyEditResult(
                    success=False,
                    content=original,
                    failure_reason=f"Edit {i+1}: SEARCH block contains only blank lines",
                    failed_search=old_str,
                    parser_format=parser_format,
                )

            n = len(search_lines)
            file_lines = result.splitlines(True)
            file_lines_stripped = [re.sub(r'\s+', ' ', l.strip()) for l in file_lines]

            matches = []
            for j in range(len(file_lines_stripped) - n + 1):
                if file_lines_stripped[j:j + n] == normalized_search:
                    matches.append(j)

            if len(matches) == 1:
                match_start = matches[0]
                original_window = "".join(file_lines[match_start:match_start + n])
                result = result.replace(original_window, new_str, 1)
                applied_count += 1
                logger.info(
                    f"  edit_loop edit {i+1}/{len(edits)} applied: Whitespace-normalized window"
                )
                continue
            elif len(matches) > 1:
                logger.warning(
                    f"  edit_loop edit {i+1}/{len(edits)} ambiguous after whitespace normalization: {len(matches)} matches"
                )
                return ApplyEditResult(
                    success=False,
                    content=original,
                    failure_reason=f"SEARCH block matched multiple locations after whitespace normalization ({len(matches)} matches, ambiguous)",
                    failed_search=old_str,
                    parser_format=parser_format,
                )
            else:
                logger.warning(
                    f"  edit_loop edit {i+1}/{len(edits)} failed: SEARCH block did not match"
                )
                return ApplyEditResult(
                    success=False,
                    content=original,
                    failure_reason="SEARCH block did not match the current file",
                    failed_search=old_str,
                    parser_format=parser_format,
                )

        return ApplyEditResult(
            success=True,
            content=result,
            applied_count=applied_count,
            parser_format=parser_format,
        )

    def _apply_search_replace(
        self, original: str, llm_response: str, is_create: bool
    ) -> ApplyEditResult:
        """Apply additive insertion strategies or SEARCH/REPLACE from LLM response.

        Returns structured ApplyEditResult with success status, failure reason,
        and parser format.
        """
        if is_create or not original:
            code_m = re.search(r'```[\w]*\n(.*?)```', llm_response, re.DOTALL)
            if code_m:
                clean = code_m.group(1)
            else:
                clean = re.sub(r'^```[\w]*\n|```$', '', llm_response.strip(), flags=re.MULTILINE)
            if clean.strip():
                return ApplyEditResult(
                    success=True,
                    content=clean,
                    applied_count=1,
                    parser_format="create",
                )
            return ApplyEditResult(
                success=False,
                content=original,
                failure_reason="Empty content generated for file creation",
                parser_format="create",
            )

        stripped = llm_response.strip()
        if not stripped:
            return ApplyEditResult(
                success=False,
                content=original,
                failure_reason="No edit requested (empty LLM response)",
                parser_format="none",
            )

        # ── Primary: Canonical Aider-style SEARCH/REPLACE blocks ──────────
        aider_pattern = re.compile(
            r'<{7} SEARCH\s*\n(.*?)\n={7}\s*\n(.*?)\n>{7} REPLACE',
            re.DOTALL,
        )
        aider_matches = list(aider_pattern.finditer(llm_response))
        if aider_matches:
            edits = []
            for idx, m in enumerate(aider_matches):
                old_s = m.group(1)
                new_s = m.group(2)
                if re.search(r'<{7} SEARCH|>{7} REPLACE', new_s):
                    return ApplyEditResult(
                        success=False,
                        content=original,
                        failure_reason=f"Edit {idx+1}: REPLACE block contains nested SEARCH/REPLACE markers",
                        failed_search=old_s,
                        parser_format="aider",
                    )
                edits.append({"old_str": old_s, "new_str": new_s})
            return self._safe_match_and_replace(original, edits, parser_format="aider")

        # ── Fallback 1: <<<SEARCH>>>/<<<REPLACE>>>/<<<END>>> format ────────
        delimiter_pattern = re.compile(
            r'<<<SEARCH>>>\s*\n?(.*?)\n?<<<REPLACE>>>\s*\n?(.*?)\n?<<<END>>>',
            re.DOTALL,
        )
        delimiter_matches = list(delimiter_pattern.finditer(llm_response))
        if delimiter_matches:
            edits = []
            for idx, m in enumerate(delimiter_matches):
                old_s = m.group(1)
                new_s = m.group(2)
                if "<<<SEARCH>>>" in new_s or "<<<REPLACE>>>" in new_s:
                    return ApplyEditResult(
                        success=False,
                        content=original,
                        failure_reason=f"Edit {idx+1}: REPLACE block contains nested SEARCH/REPLACE markers",
                        failed_search=old_s,
                        parser_format="delimiters",
                    )
                edits.append({"old_str": old_s, "new_str": new_s})
            return self._safe_match_and_replace(original, edits, parser_format="delimiters")

        # ── Fallback 2: legacy <<</>>> EDIT blocks ────────────────────────
        legacy_pattern = re.compile(
            r'EDIT:\s*\n'
            r'old_str:\s*\n<<<\n(.*?)\n>>>\s*\n'
            r'new_str:\s*\n<<<\n(.*?)\n>>>',
            re.DOTALL,
        )
        legacy_matches = list(legacy_pattern.finditer(llm_response))
        if legacy_matches:
            edits = []
            for idx, m in enumerate(legacy_matches):
                old_s = m.group(1)
                new_s = m.group(2)
                if "EDIT:\nold_str:" in new_s:
                    return ApplyEditResult(
                        success=False,
                        content=original,
                        failure_reason=f"Edit {idx+1}: new_str contains nested EDIT block",
                        failed_search=old_s,
                        parser_format="legacy",
                    )
                edits.append({"old_str": old_s, "new_str": new_s})
            return self._safe_match_and_replace(original, edits, parser_format="legacy")

        return ApplyEditResult(
            success=False,
            content=original,
            failure_reason="No recognized SEARCH/REPLACE blocks found in response",
            parser_format="none",
        )

    # ── Cross-file error detection helpers ────────────────────────────────────
    # These parse DETERMINISTIC COMPILER OUTPUT (not keyword lists).
    # The TypeScript compiler outputs structured error messages with exact
    # property names and type names — this is machine output like exit codes.
    #
    # Symbol resolution is delegated to SymbolResolver (symbol_resolver.py),
    # a shared program-understanding primitive. The queuing logic stays here
    # because it's edit-loop-specific.

    # Regex for TypeScript compiler's "does not exist on type" error format.
    # This is part of the TS compiler specification, not a keyword guess.
    _CROSS_FILE_RE = re.compile(
        r"Property\s+'(\w+)'\s+does not exist on type\s+'(\w+)'"
    )

    def _extract_cross_file_targets(
        self, errors: list[str], current_file: str
    ) -> dict[str, list[str]]:
        """Parse compiler errors to find cross-file dependencies.

        Reads COMPILER OUTPUT (deterministic machine format) to detect when
        errors reference a type/class defined in a different file.
        Uses SymbolResolver to trace type names to file paths.

        Returns: {target_file_path: [missing_member_1, missing_member_2]}
        """
        targets: dict[str, list[str]] = {}
        norm_current = current_file.replace("\\", "/").lower()

        for err in errors:
            m = self._CROSS_FILE_RE.search(err)
            if not m:
                continue
            member_name = m.group(1)
            type_name = m.group(2)

            # Delegate symbol resolution to the shared SymbolResolver
            type_file = self._resolver.get_file_for_type(type_name)

            if type_file:
                norm_type = type_file.replace("\\", "/").lower()
                if norm_type != norm_current:
                    targets.setdefault(type_file, []).append(member_name)

        return targets

    def _is_cross_file_error(self, error: str, current_file: str) -> bool:
        """Check if a compiler error references a type in a different file."""
        m = self._CROSS_FILE_RE.search(error)
        if not m:
            return False
        type_name = m.group(2)
        type_file = self._resolver.get_file_for_type(type_name)
        if not type_file:
            return False
        return not self._resolver.is_defined_in(type_name, current_file)

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
            errors = _check_html_syntax(content, file_path) if content else []

            # ── Cross-file template binding validation ────────────────────
            # Catches mismatched property names (e.g. organizationName vs
            # existingOrganizationName) by resolving Angular template bindings
            # against the sibling TS controller + repository/generated truth.
            if content:
                ref_errors = self._validate_template_bindings(file_path, content)
                errors.extend(ref_errors)

            return errors
        # Java and C# verification is handled by the full build phase
        return []

    def _validate_template_bindings(self, file_path: str, content: str) -> list[str]:
        """Validate Angular template bindings against the generated workspace.

        Uses GeneratedReferenceValidator.validate_template() to extract bindings
        and resolve them against:
        1. SymbolResolver (repository truth)
        2. ImplementationState (generated symbols)
        3. _written_files (freshly generated content)

        Returns compiler-like error strings that feed into the existing
        inline-fix loop.
        """
        from ticket_to_code.agents.generated_reference_validator import (
            GeneratedReferenceValidator,
        )

        validator = GeneratedReferenceValidator(
            symbol_resolver=self._resolver,
            impl_state=self._impl_state,
        )

        result = validator.validate_template(
            html_content=content,
            html_file_path=file_path,
            written_files=self._written_files,
        )

        errors: list[str] = []
        for check in result.unresolved:
            close_hint = ""
            if check.close_match:
                close_hint = f" Did you mean '{check.close_match}'?"
            owner_hint = f" on type '{check.resolved_type}'" if check.resolved_type else ""
            errors.append(
                f"Template binding error in {Path(file_path).name}: "
                f"'{check.receiver}.{check.member}' — "
                f"member '{check.member}' is unresolved{owner_hint}.{close_hint}"
            )

        if errors:
            logger.warning(
                f"  🔗 Template binding validation found {len(errors)} "
                f"unresolved reference(s) in {Path(file_path).name}"
            )
            for e in errors:
                logger.warning(f"    ⚠️ {e}")

        return errors

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
        """Write file to disk and update session map + ImplementationState."""
        abs_path = self.workspace_path / file_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        self._written_files[file_path.replace("\\", "/").lower()] = content
        # Sync to ImplementationState so blueprint verification can see this content
        if self._impl_state is not None:
            self._impl_state.update_generated_file(file_path, content)
        logger.info(f"  📝 Written: {file_path}")
        
        file_basename = Path(file_path).name
        self._ui_emit(
            "file_written",
            message=f"📝 Written: {file_basename}",
            file_path=file_path,
            file_name=file_basename,
        )
