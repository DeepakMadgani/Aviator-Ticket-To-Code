"""
Evidence Collection Loop

Iteratively queries ALL available evidence sources until a confidence
threshold is reached (default 0.70).  Uses every piece of existing
infrastructure - nothing is replaced:

  * Semantic search    -> RAGEngine.retrieve_context()
  * Neo4j graph        -> LocalizationAgent.neo4j_store
  * SQLite symbols     -> LocalizationAgent.sqlite_store (FTS5 + edges)
  * Java chain         -> _graph_neighbors (Spring boot architecture)
  * UI i18n chain      -> Full mapping via _extract_ui_text_labels
  * Typescript chain   -> Component->Template->Styles graph

The loop starts with an aggressive Stage 0 to trace UI strings directly
to components, then iteratively searches literals/symbols, then escalates
to graph and RAG if needed, dynamically constructing hypotheses.
"""

import os
import json
import logging
import re
from typing import List, Tuple, Dict, Set, Optional, Any, TYPE_CHECKING
from pathlib import Path
from dataclasses import dataclass, field
from aviator.services.llm import LLMRegistry
from langchain_core.messages import HumanMessage, SystemMessage

from ticket_to_code.models import *

if TYPE_CHECKING:
    from ticket_to_code.agents.repository_search_engine import RepositorySearchEngine
    from ticket_to_code.agents.providers.relationship_provider import RelationshipProvider
    from ticket_to_code.agents.localization_agent import LocalizationAgent
    from ticket_to_code.agents.rag_engine import CodebaseRAGEngine

logger = logging.getLogger(__name__)

# Constants and regular expressions that were lost during cleanup
_CONFIDENCE_THRESHOLD = 0.70
_TITLE_CASE_RE = re.compile(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b')
_QUOTED_RE = re.compile(r'["\']([^"\']+)["\']')
_VISUAL_MARKER_RE = re.compile(r'[^a-zA-Z0-9\s]')
_STOP_LABELS = {'the', 'a', 'an', 'and', 'or', 'but'}
_JSON_KV_RE = re.compile(r'"([^"]+)"\s*:\s*"([^"]+)"')
_PROPS_KV_RE = re.compile(r'^([^=]+)=(.*)$')
_EXPORT_RE = re.compile(r'export\s+(?:class|const|function)\s+([a-zA-Z0-9_]+)')

# -- Tuning Constants ---------------------------------------------------------
_CONFIDENCE_THRESHOLD = 0.70

_W_RAG      = 0.30
_W_LITERAL  = 0.25
_W_SQLITE   = 0.25
_W_NEO4J    = 0.15
_W_TS_CHAIN = 0.10
_W_JAVA_CHAIN = 0.10
_W_CSS_CHAIN  = 0.10
_W_CONFIG_CHAIN = 0.05
_W_LAYOUT_CHAIN = 0.05

# Config / property file extensions (application-level only; .json data files
# are intentionally excluded - they match almost every word)
_CONFIG_EXTS = frozenset({
    '.properties', '.yaml', '.yml', '.xml',
    '.env', '.conf', '.ini', '.toml',
})

# Subdirectory path segments that are NEVER meaningful config locations
_CONFIG_SKIP_SEGMENTS = frozenset({
    'traces', 'assets', 'postman', '.idea', '__pycache__',
    'node_modules', 'dist', 'build', '.angular', 'coverage',
})

# Minimum literal length for config chain search - short words like 'Add',
# 'Cancel', 'filter' match almost everywhere and produce noise.
_CONFIG_MIN_LITERAL_LEN = 6

# Minimum literal length for live repository search - shorter than config chain
# to capture version numbers like '26.2' (4 chars).
_REPO_SEARCH_MIN_LITERAL_LEN = 4
# Java stereotype annotations
_JAVA_STEREOTYPES = frozenset({
    'Controller', 'RestController', 'Service', 'Component',
    'Repository', 'Configuration', 'Bean',
})

# -- Agentic Loop constants ------------------------------------------
_AGENTIC_MAX_ITERATIONS = 6

# Tool catalog - prompt content the LLM sees when deciding which tool to use.
# Not code-level branching. The model reads this, picks a tool, picks a query.
TOOL_CATALOG = """
Available search tools (pick exactly ONE per iteration):

| Tool Name | Best For | Latency |
|-----------|----------|---------|
| ripgrep | Exact text: error messages, variable names, config values, i18n keys | <1s |
| filename_search | Finding files by name fragment (e.g. "deliverable", "contract-member") | <1s |
| regex_search | Pattern matching: version numbers, URL patterns, annotations | <1s |
| sqlite_fts | Tokenized full-text search on indexed code content | <1s |
| symbol_lookup | Class names, method names, decorators, injected services | <1s |
| neo4j_graph | Import/call/extends/implements relationships between files | 1-3s |
| ts_chain | Angular structural edges: component.ts -> .html -> .scss -> .spec.ts | 1-2s |
| java_chain | Spring layers: Controller -> Service -> Repository -> Entity/DTO | 1-2s |
| python_chain | Python module import chains | 1-2s |
| css_chain | CSS/SCSS inheritance and shared class chains | 1-2s |
| config_chain | Property/YAML/XML configuration file chains | 1-2s |
| layout_chain | Angular routing and layout container chains | 1-2s |
| semantic_rag | Conceptual/intent-based search when exact terms are unknown | 3-5s |
| i18n_chain | UI error text -> translation key -> source component that uses it | <1s |
"""

class EvidenceCollectionLoop:

    """

    Runs the iterative evidence collection loop for a ticket.

    Uses existing LocalizationAgent infrastructure (sqlite_store,

    neo4j_store, _graph_neighbors) and RAGEngine — no new storage layers.

    Usage::

        loop = EvidenceCollectionLoop(localizer, rag_engine)

        items, confidence = loop.collect(hypotheses, localized_tasks, ticket)

    """

    def __init__(

        self,

        localizer: "LocalizationAgent",

        rag_engine: "CodebaseRAGEngine",

        repo_search: Optional["RepositorySearchEngine"] = None,

        relationship_provider: Optional["RelationshipProvider"] = None,

    ) -> None:

        self.localizer = localizer

        self.rag_engine = rag_engine

        self.repo_search = repo_search   # Phase 3A: live filesystem search

        self.relationship_provider = relationship_provider

        self._workspace = localizer.workspace_path

    # =========================================================================

    # PUBLIC ENTRY POINT

    # =========================================================================

    def collect(

        self,

        ticket: ValueEdgeTicket,

        hypotheses: Optional[List[Any]] = None,

        localized_tasks: Optional[List[Any]] = None,

        technical_facts: Optional[Any] = None,

        investigation: Optional[Any] = None,

        search_scope: Optional[Any] = None,

        run_ctx=None,

    ) -> Tuple[List[EvidenceItem], float]:

        """

        Run evidence collection until confidence >= threshold or budget exhausted.

        Implements V3 Iterative Discovery Engine (Milestone 1).

        """

        import time

        import json

        import os

        print("\n" + "=" * 80)

        print("[ENTER] EvidenceCollectionLoop.collect() [V3 Iterative Engine]")

        print("=" * 80)

        all_evidence: List[EvidenceItem] = []

        all_code_facts: List[Any] = []

        confidence: float = 0.0

        

        # Initialize Central SearchContext

        context = SearchContext(ticket_facts=technical_facts)

        if localized_tasks:

            for t in localized_tasks:

                context.visited_files.add(t.file_path.replace("\\", "/"))

        

        # Initial search anchors from TechnicalFacts

        SearchAnchor = __import__('ticket_to_code.models', fromlist=['SearchAnchor']).SearchAnchor

        if technical_facts:

            # Add explicit literals

            for lit in technical_facts.high_priority_literals:

                context.anchor_history.append(

                    SearchAnchor(name=lit, kind="literal", source_file="ticket")

                )

            # Add explicit files

            for file_fact in technical_facts.files:

                context.anchor_history.append(

                    SearchAnchor(name=file_fact.name, kind="filename", source_file="ticket", score=10)

                )

        

        # FIX: Seed anchors from hypotheses and ticket when technical_facts is absent.

        # In LangGraph pipeline mode, technical_facts is never populated, so the evidence

        # loop would start with 0 anchors → 0 evidence → workflow dies.

        if not context.anchor_history:

            logger.info("  ðŸ“Œ No TechnicalFacts — seeding anchors from hypotheses & ticket")

            # Extract class/method/file names from hypotheses

            if hypotheses:

                for h in hypotheses[:4]:

                    hyp_text = getattr(h, "hypothesis", str(h))

                    # Extract PascalCase class names (e.g. ContractMemberService)

                    class_names = re.findall(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b', hyp_text)

                    for cn in class_names[:3]:

                        context.anchor_history.append(

                            SearchAnchor(name=cn, kind="class", source_file="hypothesis", score=8)

                        )

                    # Extract camelCase method names (e.g. saveMember)

                    method_names = re.findall(r'\b([a-z]+(?:[A-Z][a-z]+)+)\b', hyp_text)

                    for mn in method_names[:3]:

                        context.anchor_history.append(

                            SearchAnchor(name=mn, kind="method", source_file="hypothesis", score=7)

                        )

                    # Use hypothesis text as a broad literal search anchor

                    if hyp_text and len(hyp_text) > 10:

                        context.anchor_history.append(

                            SearchAnchor(name=hyp_text[:100], kind="literal", source_file="hypothesis", score=6)

                        )

            # Extract class/file names from localized tasks

            if localized_tasks:

                for t in localized_tasks[:5]:

                    fp = getattr(t, "file_path", "")

                    # Use the class name from the localization result

                    cls_name = getattr(t, "target_class", None)

                    if cls_name:

                        context.anchor_history.append(

                            SearchAnchor(name=cls_name, kind="class", source_file="localized_task", score=9)

                        )

                    # Use filename stem as a symbol anchor

                    stem = Path(fp).stem if fp else None

                    if stem and len(stem) > 3:

                        context.anchor_history.append(

                            SearchAnchor(name=stem, kind="class", source_file="localized_task", score=8)

                        )

            # Final fallback: use ticket title as a broad search

            if not context.anchor_history and ticket:

                title = getattr(ticket, "title", "") or ""

                if title:

                    context.anchor_history.append(

                        SearchAnchor(name=title[:100], kind="literal", source_file="ticket_title", score=5)

                    )

            

            logger.info(f"  ðŸ“Œ Seeded {len(context.anchor_history)} anchors from hypotheses/tasks/ticket")

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

        # STAGE 0: UI TEXT → i18n CHAIN SEARCH

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

        # Fires on ALL input sources: ticket title, ticket description,

        # visual screenshots, and hypotheses.  Traces UI labels through

        # i18n translation files (Angular/React/Java) to find the actual

        # source component.  Runs BEFORE the main loop so all 14 existing

        # search sources benefit from the high-confidence anchors.

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

        try:

            ui_labels = self._extract_ui_text_labels(ticket, hypotheses)

            if ui_labels:

                logger.info(

                    f"  ðŸ” Stage 0: Extracted {len(ui_labels)} UI text labels "

                    f"from ticket/visual/hypotheses"

                )

                for label in ui_labels:

                    # Add as high-priority anchor for the main loop

                    context.anchor_history.append(

                        SearchAnchor(

                            name=label, kind="visual_text",

                            source_file="ui_label", score=9,

                        )

                    )

                    # Run i18n chain tracing (Angular/React/Java)

                    chain_items = self._trace_i18n_chain(

                        label, context.visited_files

                    )

                    if chain_items:

                        logger.info(

                            f"    ðŸ”— i18n chain '{label}': "

                            f"{len(chain_items)} component(s) found"

                        )

                        all_evidence.extend(chain_items)

                        for e in chain_items:

                            context.visited_files.add(e.file_path)

                            # Also seed new anchors from chain results

                            stem = Path(e.file_path).stem

                            if stem and len(stem) > 3:

                                context.anchor_history.append(

                                    SearchAnchor(

                                        name=stem, kind="class",

                                        source_file="i18n_chain", score=9,

                                    )

                                )

                if all_evidence:

                    logger.info(

                        f"  ðŸ” Stage 0 complete: {len(all_evidence)} files "

                        f"found via i18n chain tracing"

                    )

            else:

                logger.info("  ðŸ” Stage 0: No UI text labels extracted — skipping i18n chain")

        except Exception as stage0_exc:

            logger.warning(f"  âš ï¸  Stage 0 (i18n chain) failed (non-fatal): {stage0_exc}")

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

        # AGENTIC SEARCH LOOP

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

        # Instead of a fixed sequence (literals → symbols → filenames →

        # graph → RAG), the LLM decides per iteration which tool to call,

        # evaluates results, and either keeps searching, proceeds, or

        # asks the user for clarification.

        #

        # State:

        #   evidence    — file_path → {relevant, reason, decision, ...}

        #   all_items   — full EvidenceItem objects (returned to caller)

        #   tried       — [{tool, query, files_returned, iteration}]

        #   trace_log   — per-iteration debugging trace

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

        evidence: Dict[str, dict] = {}

        all_items: List[EvidenceItem] = list(all_evidence)  # seed from Stage 0 (i18n chain)

        tried: List[dict] = []

        trace_log: List[dict] = []

        # Pre-populate evidence for Stage 0 results (i18n chain items are

        # high-confidence — include by default, no LLM verification needed)

        for item in all_evidence:

            evidence[item.file_path] = {

                "relevant": True,

                "reason": "Found via i18n chain (Stage 0 bootstrap)",

                "file_path": item.file_path,

                "decision": "include",

                "semantic_relevance_score": item.relevance_score,

            }

        logger.info(

            f"  🤖 Starting agentic loop: {_AGENTIC_MAX_ITERATIONS} max iterations, "

            f"{len(all_evidence)} pre-seeded from Stage 0"

        )

        for iteration in range(_AGENTIC_MAX_ITERATIONS):

            # ── LLM decides next action ──────────────────────────────────

            action = self._decide_next_action(

                ticket=ticket,

                hypotheses=hypotheses,

                evidence=evidence,

                tried=tried,

                all_items=all_items,

                iteration=iteration,

                max_iterations=_AGENTIC_MAX_ITERATIONS,

            )

            action_type = action.get("type", "proceed")

            iteration_entry = {

                "iteration": iteration + 1,

                "action_type": action_type,

                "tool": action.get("tool"),

                "query": action.get("query"),

                "reasoning": action.get("reasoning", ""),

                "files_returned": [],

                "verdicts": [],

            }

            # ── PROCEED — evidence is sufficient ─────────────────────────

            if action_type == "proceed":

                logger.info(

                    f"  ✅ Agentic loop → proceed at iteration {iteration + 1}: "

                    f"{action.get('reasoning', '')[:100]}"

                )

                trace_log.append(iteration_entry)

                break

            # ── ASK_USER — can't find files with confidence ──────────────

            if action_type == "ask_user":

                question = action.get("question", "Could not determine which files need changes.")

                logger.info(

                    f"  â“ Agentic loop → ask_user at iteration {iteration + 1}: "

                    f"{question[:100]}"

                )

                trace_log.append(iteration_entry)

                # Write trace before returning

                trace_dir = os.path.join("C:\\", "aviator_traces", ticket.ticket_id)

                os.makedirs(trace_dir, exist_ok=True)

                try:

                    with open(os.path.join(trace_dir, "agentic_trace.json"), "w") as f:

                        json.dump(trace_log, f, indent=2)

                except Exception:

                    pass

                return self._build_need_more_info_return(

                    question, evidence, all_items, tried

                )

            # ── SEARCH — run the chosen tool ─────────────────────────────

            tool_name = action.get("tool", "")

            query = action.get("query", "")

            if not tool_name or not query:

                logger.warning(

                    f"[AgenticLoop] Search action missing tool/query: {action}"

                )

                trace_log.append(iteration_entry)

                continue

            new_files = self._execute_tool(tool_name, query, context)

            tried.append({

                "tool": tool_name,

                "query": query,

                "files_returned": [f.file_path for f in new_files],

                "iteration": iteration + 1,

            })

            iteration_entry["files_returned"] = [f.file_path for f in new_files]

            # ── Verify each new file ─────────────────────────────────────

            for file_item in new_files:

                all_items.append(file_item)

                if file_item.file_path not in evidence:

                    verdict = self._verify_single_file(

                        ticket, file_item, evidence

                    )

                    evidence[file_item.file_path] = verdict

                    iteration_entry["verdicts"].append({

                        "file": file_item.file_path,

                        "decision": verdict.get("decision", "unknown"),

                        "reason": verdict.get("reason", "")[:100],

                    })

                    all_evidence.append(file_item)

            trace_log.append(iteration_entry)

            # Log iteration summary

            n_relevant = sum(1 for v in evidence.values() if v.get("relevant"))

            n_excluded = sum(1 for v in evidence.values() if not v.get("relevant"))

            logger.info(

                f"  ðŸ“Š After iteration {iteration + 1}: "

                f"{n_relevant} relevant, {n_excluded} excluded, "

                f"{len(tried)} searches tried"

            )

        else:

            # Budget exhausted without "proceed" — ask_user, never force

            logger.warning(

                f"  âš ï¸ Agentic loop exhausted {_AGENTIC_MAX_ITERATIONS} iterations "

                f"without confident evidence — routing to ask_user"

            )

            # Write trace

            trace_dir = os.path.join("C:\\", "aviator_traces", ticket.ticket_id)

            os.makedirs(trace_dir, exist_ok=True)

            try:

                with open(os.path.join(trace_dir, "agentic_trace.json"), "w") as f:

                    json.dump(trace_log, f, indent=2)

            except Exception:

                pass

            return self._build_need_more_info_return(

                "Could not identify the relevant source files with confidence "

                "after exhausting the search budget. Please provide more specific "

                "details about which component, page, or feature is affected.",

                evidence, all_items, tried,

            )

        # ── Write agentic trace ──────────────────────────────────────────

        trace_dir = os.path.join("C:\\", "aviator_traces", ticket.ticket_id)

        os.makedirs(trace_dir, exist_ok=True)

        try:

            with open(os.path.join(trace_dir, "agentic_trace.json"), "w") as f:

                json.dump(trace_log, f, indent=2)

        except Exception:

            pass

        self._last_code_facts = all_code_facts

        logger.info(

            f"  Evidence collection done: {len(all_evidence)} items, "

            f"{sum(1 for v in evidence.values() if v.get('relevant'))} verified relevant"

        )

        # ── B6: Evidence Promotion Validation ────────────────────────────

        # Filter raw evidence to only items that genuinely support at least

        # one hypothesis or have strong multi-source backing.

        validated_evidence = self._validate_evidence_promotion(

            all_evidence, hypotheses or []

        )

        if len(validated_evidence) < len(all_evidence):

            logger.info(

                "  B6 promotion filter: kept %d / %d evidence items "

                "(removed %d low-quality items)",

                len(validated_evidence), len(all_evidence),

                len(all_evidence) - len(validated_evidence),

            )

        # Recompute confidence on validated set

        if validated_evidence:

            confidence = self._compute_confidence(

                validated_evidence, hypotheses or [], localized_tasks or []

            )

        else:

            confidence = 0.0

        # Store the per-file verification verdicts so the workflow can

        # populate semantic_verification_results in the expected batch shape

        # (list of {file_path, decision, semantic_relevance_score, reason})

        # without re-running the old batch verifier.

        self._agentic_verdicts = evidence

        return validated_evidence, confidence, None

    # =========================================================================

    # AGENTIC LOOP — CORE METHODS

    # =========================================================================

    def _decide_next_action(

        self,

        ticket: "ValueEdgeTicket",

        hypotheses: Optional[List[Any]],

        evidence: Dict[str, dict],

        tried: List[dict],

        all_items: List[EvidenceItem],

        iteration: int,

        max_iterations: int,

    ) -> dict:

        """

        Single LLM call: given ticket + evidence so far + tried tools,

        pick exactly one action:

          {"type": "search", "tool": "<tool_name>", "query": "<query>", "reasoning": "..."}

          {"type": "proceed", "reasoning": "..."}

          {"type": "ask_user", "question": "...", "reasoning": "..."}

        """

        from ticket_to_code.json_utils import parse_llm_json

        # Build context for the LLM

        ticket_title = getattr(ticket, "title", "") or ""

        ticket_desc = getattr(ticket, "description", "") or ""

        # Summarize evidence found so far

        included = [fp for fp, v in evidence.items() if v.get("relevant")]

        excluded = [fp for fp, v in evidence.items() if not v.get("relevant")]

        evidence_summary = ""

        if included:

            evidence_summary += "Files confirmed RELEVANT so far:\n"

            for fp in included:

                evidence_summary += f"  ✅ {fp} — {evidence[fp].get('reason', '')[:100]}\n"

        if excluded:

            evidence_summary += "Files checked and found NOT relevant:\n"

            for fp in excluded[:10]:

                evidence_summary += f"  âŒ {fp} — {evidence[fp].get('reason', '')[:100]}\n"

            if len(excluded) > 10:

                evidence_summary += f"  ... and {len(excluded) - 10} more\n"

        if not evidence:

            evidence_summary = "No files verified yet.\n"

        # Summarize what was already tried

        tried_summary = ""

        for t in tried:

            n_files = len(t.get("files_returned", []))

            tried_summary += f"  - [{t['tool']}] query={t['query']!r} → {n_files} file(s)\n"

        if not tried:

            tried_summary = "  (nothing tried yet)\n"

        # Hypothesis context

        hyp_text = ""

        if hypotheses:

            for h in hypotheses[:3]:

                hyp_str = getattr(h, "hypothesis", str(h))

                hyp_text += f"  - {hyp_str[:200]}\n"

        prompt = f"""You are an evidence-collection agent for a code-change ticket.

Your job: find the SOURCE CODE FILES that need to be MODIFIED to resolve this ticket.

TICKET:

  Title: {ticket_title}

  Description: {ticket_desc[:500]}

HYPOTHESES (what the investigation agent thinks needs to change):

{hyp_text or '  (none)'}

{TOOL_CATALOG}

EVIDENCE COLLECTED SO FAR:

{evidence_summary}

SEARCHES ALREADY TRIED (do NOT repeat):

{tried_summary}

ITERATION: {iteration + 1} of {max_iterations}

Decide your next action. Return EXACTLY ONE JSON object:

Option 1 — Search for more evidence:

{{"type": "search", "tool": "<tool_name from catalog>", "query": "<search query>", "reasoning": "<why this tool and query>"}}

Option 2 — You have enough evidence to proceed to planning:

{{"type": "proceed", "reasoning": "<why evidence is sufficient>"}}

Option 3 — You genuinely cannot find the relevant files (ticket is too vague, missing info):

{{"type": "ask_user", "question": "<specific question for the ticket author>", "reasoning": "<why you're stuck>"}}

Rules:

- Do NOT repeat a (tool, query) pair from SEARCHES ALREADY TRIED.

- Use "proceed" when you have at least one RELEVANT file with strong evidence.

- Use "ask_user" ONLY when you've tried multiple approaches and still can't find relevant files.

- Pick the tool best suited for the signal available. Error messages → ripgrep/i18n_chain. Class names → symbol_lookup. Relationships → ts_chain/java_chain.

- Be specific with queries. Don't search for generic terms like "error" or "component".

Return ONLY the JSON object, no other text."""

        try:

            llm = self._get_agentic_llm()

            if not llm:

                logger.warning("[AgenticLoop] No LLM available — falling back to proceed")

                return {"type": "proceed", "reasoning": "LLM unavailable, proceeding with current evidence"}

            response = llm.invoke(prompt)

            text = response.content if hasattr(response, "content") else str(response)

            action = parse_llm_json(text)

            if not isinstance(action, dict) or "type" not in action:

                logger.warning(f"[AgenticLoop] Invalid action format: {text[:200]}")

                return {"type": "proceed", "reasoning": "Could not parse LLM action"}

            action_type = action.get("type", "proceed")

            if action_type not in ("search", "proceed", "ask_user"):

                action["type"] = "proceed"

            logger.info(

                f"  🤖 Agentic iteration {iteration + 1}: "

                f"action={action['type']}"

                + (f" tool={action.get('tool')}" if action["type"] == "search" else "")

                + f" | {action.get('reasoning', '')[:80]}"

            )

            return action

        except Exception as e:

            logger.warning(f"[AgenticLoop] LLM decision failed: {e} — proceeding")

            return {"type": "proceed", "reasoning": f"LLM error: {e}"}

    def _get_agentic_llm(self):
        """Get or create the LLM for agentic decisions.

        Uses LLMRegistry.get_llm() — the same path every other agent in the
        pipeline uses (TicketAnalyzer, PlanningAgent, InvestigationAgent, etc.).
        The previous implementation tried to create its own ChatGoogleGenerativeAI
        with GOOGLE_API_KEY env var, but the backend runs on Vertex AI where
        that env var doesn't exist, so the LLM was always None.
        """
        if hasattr(self, "_agentic_llm") and self._agentic_llm is not None:
            return self._agentic_llm

        try:
            from aviator.services.llm import LLMRegistry
            self._agentic_llm = LLMRegistry.get_llm(assistant=False)
            if self._agentic_llm:
                logger.info("[AgenticLoop] LLM initialized via LLMRegistry")
            else:
                logger.warning("[AgenticLoop] LLMRegistry.get_llm() returned None")
            return self._agentic_llm
        except Exception as e:
            logger.warning(f"[AgenticLoop] Could not create LLM via LLMRegistry: {e}")
            self._agentic_llm = None
            return None

    def _execute_tool(

        self,

        tool_name: str,

        query: str,

        context: "SearchContext",

    ) -> List[EvidenceItem]:

        """

        Dispatcher: maps a tool name string to the existing search method.

        No new tools. Returns a list of EvidenceItem objects.

        """

        items: List[EvidenceItem] = []

        try:

            if tool_name == "ripgrep":

                if self.repo_search and len(query.strip()) >= _REPO_SEARCH_MIN_LITERAL_LEN:

                    results = self.repo_search.search_literal(query)

                    for r in results:

                        if r.file_path not in context.visited_files:

                            items.append(EvidenceItem(

                                provider="literal", strength="strong",

                                details="agentic_ripgrep",

                                file_path=r.file_path,

                                evidence_type="usage",

                                content_snippet=f"[ripgrep:{query!r}] line {r.line_number}: {r.matched_text}"[:400],

                                relevance_score=min(float(r.confidence), 1.0),

                                hypothesis_id="agentic",

                            ))

            elif tool_name == "filename_search":

                if self.repo_search:

                    results = self.repo_search.search_filename(query)

                    for r in results:

                        if r.file_path not in context.visited_files:

                            items.append(EvidenceItem(

                                provider="filename", strength="medium",

                                details="agentic_filename",

                                file_path=r.file_path,

                                evidence_type="structural",

                                content_snippet=f"[filename:{query!r}] {r.file_path}",

                                relevance_score=min(float(r.confidence), 1.0),

                                hypothesis_id="agentic",

                            ))

            elif tool_name == "regex_search":

                if self.repo_search:

                    results = self.repo_search.search_regex(query)

                    for r in results:

                        if r.file_path not in context.visited_files:

                            items.append(EvidenceItem(

                                provider="regex", strength="medium",

                                details="agentic_regex",

                                file_path=r.file_path,

                                evidence_type="usage",

                                content_snippet=f"[regex:{query!r}] line {r.line_number}: {r.matched_text}"[:400],

                                relevance_score=min(float(r.confidence), 1.0),

                                hypothesis_id="agentic",

                            ))

            elif tool_name == "sqlite_fts":

                items.extend(self._query_sqlite_fts([query], "agentic", context.visited_files))

            elif tool_name == "symbol_lookup":

                items.extend(self._query_sqlite_symbols([query], "agentic", context.visited_files))

            elif tool_name == "neo4j_graph":

                items.extend(self._query_neo4j([query], "agentic", context.visited_files))

            elif tool_name == "ts_chain":

                # query should be a file path seed

                items.extend(self._follow_ts_chains([query], context.visited_files))

            elif tool_name == "java_chain":

                items.extend(self._follow_java_chains([query], context.visited_files))

            elif tool_name == "python_chain":

                items.extend(self._follow_python_chains([query], context.visited_files))

            elif tool_name == "css_chain":

                items.extend(self._follow_css_chains([query], context.visited_files))

            elif tool_name == "config_chain":

                items.extend(self._follow_config_chains([query], context.visited_files))

            elif tool_name == "layout_chain":

                items.extend(self._follow_layout_chains([query], context.visited_files))

            elif tool_name == "semantic_rag":

                items.extend(self._query_semantic([query], "agentic", context.visited_files))

            elif tool_name == "i18n_chain":

                items.extend(self._trace_i18n_chain(query, context.visited_files))

            else:

                logger.warning(f"[AgenticLoop] Unknown tool: {tool_name}")

        except Exception as e:

            logger.warning(f"[AgenticLoop] Tool {tool_name} failed: {e}")

        # Track visited files

        for item in items:

            context.visited_files.add(item.file_path)

        logger.info(f"  ðŸ”§ Tool [{tool_name}] query={query!r} → {len(items)} file(s)")

        return items

    def _verify_single_file(

        self,

        ticket: "ValueEdgeTicket",

        file_item: EvidenceItem,

        existing_evidence: Dict[str, dict],

    ) -> dict:

        """

        Verify a single file via SemanticVerificationAgent.

        Returns dict matching the shape consumers expect:

            {"relevant": bool, "reason": str,

             "file_path": str, "decision": "include"|"exclude",

             "semantic_relevance_score": float}

        The `decision` field uses the exact vocabulary the 4 downstream

        consumers read: "include" or "exclude" (not True/False).

        """

        if not hasattr(self, "_semantic_verifier") or self._semantic_verifier is None:

            # No verifier available — include by default

            return {

                "relevant": True,

                "reason": "No verifier available — included by default",

                "file_path": file_item.file_path,

                "decision": "include",

                "semantic_relevance_score": file_item.relevance_score,

            }

        try:

            result = self._semantic_verifier.verify_single(

                ticket=ticket,

                file_item=file_item,

                existing_evidence=existing_evidence,

            )

            # verify_single returns SemanticVerificationResult with

            # .decision ("include"/"exclude"), .reason, .file_path,

            # .semantic_relevance_score

            is_relevant = result.decision == "include"

            return {

                "relevant": is_relevant,

                "reason": result.reason,

                "file_path": result.file_path,

                "decision": result.decision,

                "semantic_relevance_score": result.semantic_relevance_score,

            }

        except Exception as e:

            logger.warning(f"[AgenticLoop] verify_single failed for {file_item.file_path}: {e}")

            return {

                "relevant": True,

                "reason": f"Verification error ({e}) — included by default",

                "file_path": file_item.file_path,

                "decision": "include",

                "semantic_relevance_score": file_item.relevance_score,

            }

    def _build_need_more_info_return(

        self,

        question: str,

        evidence: Dict[str, dict],

        all_items: List[EvidenceItem],

        tried: List[dict],

    ) -> Tuple[List[EvidenceItem], float, str]:

        """

        Build the 3-tuple return for the ask_user exit.

        Returns (partial_evidence, 0.0, clarification_question).

        The third element signals the caller to set

        state["status"] = "need_more_info" (matching routes.py line 1186).

        """

        # Return whatever relevant items we found (may be empty)

        relevant = [

            item for item in all_items

            if evidence.get(item.file_path, {}).get("relevant")

        ]

        logger.info(

            f"  â“ Agentic loop → ask_user: {len(relevant)} partial items, "

            f"question: {question[:100]}"

        )

        return relevant, 0.0, question

    # =========================================================================

    # SOURCE 1: SEMANTIC SEARCH (RAGEngine)

    # =========================================================================

    def _query_semantic(

        self,

        queries: List[str],

        hypothesis_id: str,

        visited: Set[str],

    ) -> List[EvidenceItem]:

        items: List[EvidenceItem] = []

        for query in queries[:6]:  # widened: surface UI/.scss/component files buried below backend

            try:

                chunks = self.rag_engine.retrieve_context(

                    query=query,

                    max_results=12,   # widened from 5 — breadth needed here, ranking de-noises

                    document_types=None,

                    schema="both",

                )

                for chunk in chunks:

                    fp = chunk.get("file_path", "")

                    if not fp or fp in visited:

                        continue

                    items.append(EvidenceItem(

                        provider="vector", strength="medium", details="semantic", file_path=fp,

                        evidence_type="usage",

                        content_snippet=str(chunk.get("content", ""))[:400],

                        relevance_score=float(chunk.get("score", 0.5)),

                        hypothesis_id=hypothesis_id,

                    ))

            except Exception as exc:

                logger.debug(f"Semantic query failed for '{query}': {exc}")

        return items

    def _hypothesis_to_semantic_queries(

        self, hypotheses: List[Any]

    ) -> List[str]:

        """Convert hypothesis descriptions into concrete semantic search queries.

        Each InvestigationHypothesis carries:

          - hypothesis  (natural language description)

          - queries     (LLM-generated semantic queries)

          - symbols     (class/method names)

          - anchors     (domain/page nouns)

        We collect ALL of these as RAG queries so the evidence loop can find

        conceptually related code — not just exact keyword matches.

        Example:

          Hypothesis: "Shared state contamination between tabs"

          → Queries: [

              "state management between component tabs",

              "BehaviorSubject shared service",

              "component state not reset on navigation",

            ]

        """

        queries: List[str] = []

        for h in (hypotheses or []):

            # 1. Use pre-built semantic queries from LLM

            for q in getattr(h, "queries", []):

                if q and len(q) > 5:

                    queries.append(q)

            # 2. Use the hypothesis description itself as a semantic query

            desc = getattr(h, "hypothesis", "") or ""

            if len(desc) > 10:

                queries.append(desc[:200])

            # 3. Use symbols as more targeted queries

            for sym in getattr(h, "symbols", []):

                if sym and len(sym) > 3:

                    queries.append(sym)

            # 4. Use anchors (domain nouns from ticket)

            for anchor in getattr(h, "anchors", []):

                if anchor and len(anchor) > 3:

                    queries.append(anchor)

        # Deduplicate preserving order, cap at 12 queries total

        seen: set = set()

        unique: List[str] = []

        for q in queries:

            key = q.strip().lower()

            if key not in seen:

                seen.add(key)

                unique.append(q.strip())

        return unique[:12]

    # =========================================================================

    # SOURCE 2: SQLITE FTS — string literal search

    # =========================================================================

    def _get_dynamic_threshold(self, term: str, base_threshold: int = 15) -> int:

        """Calculate specificity threshold based on entropy to avoid aborting valid broad searches."""

        import re

        if re.search(r'\d+\.\d+', term):

            return max(100, base_threshold)  # Version numbers are high entropy, allow broad sweep

        elif len(term) > 15:

            return max(50, base_threshold)   # Long phrases are highly specific

        return base_threshold

    def _query_sqlite_fts(

        self,

        literals: List[str],

        hypothesis_id: str,

        visited: Set[str],

    ) -> List[EvidenceItem]:

        if not self.localizer.sqlite_store or not literals:

            return []

        items: List[EvidenceItem] = []

        conn = self.localizer.sqlite_store._conn

        for literal in literals[:6]:

            try:

                # Try FTS5 table first, fall back to LIKE scan

                rows = self._fts_or_like(conn, literal)

                for path, snippet in rows:

                    if not path or path in visited:

                        continue

                    items.append(EvidenceItem(

                        provider="literal", strength="medium", details="sqlite_fts", file_path=path,

                        evidence_type="definition",

                        content_snippet=snippet[:400],

                        relevance_score=0.75,

                        hypothesis_id=hypothesis_id,

                    ))

            except Exception as exc:

                logger.debug(f"SQLite FTS for '{literal}' failed: {exc}")

        return items

    def _fts_or_like(self, conn, literal: str) -> List[Tuple[str, str]]:

        """

        Search the symbols_fts FTS5 table for ``literal``; fall back to a LIKE

        scan on the symbols table when FTS is unavailable.

        Returns list of (path, snippet).

        """

        # ── FTS5 via symbols_fts ──────────────────────────────────────────────

        try:

            # Escape FTS5 special characters in the literal

            fts_literal = literal.replace('"', '""').replace("'", "''")

            # Wrap in double quotes for a phrase search to avoid tokeniser issues

            fts_query = f'"{fts_literal}"'

            

            # Agentic Specificity Check: Abort if literal is too generic

            threshold = self._get_dynamic_threshold(literal)

            count_row = conn.execute(

                "SELECT COUNT(*) FROM symbols_fts WHERE symbols_fts MATCH ?",

                (fts_query,),

            ).fetchone()

            

            if count_row and count_row[0] > threshold:

                logger.warning(

                    f"[EvidenceLoop] ðŸ›‘ ABORT: FTS literal '{literal}' is too generic "

                    f"({count_row[0]} > {threshold} matches). Dropping to prevent noise."

                )

                return []

            rowids = conn.execute(

                "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH ? LIMIT 10",

                (fts_query,),

            ).fetchall()

            if rowids:

                placeholders = ",".join("?" * len(rowids))

                rows = conn.execute(

                    f"SELECT DISTINCT path, name FROM symbols "

                    f"WHERE rowid IN ({placeholders})",

                    [r[0] for r in rowids],

                ).fetchall()

                if rows:

                    return [(r[0], r[1]) for r in rows]

        except Exception as e:

            pass  # FTS unavailable — fall through to LIKE

        # ── Symbol-name LIKE scan (fallback) ──────────────────────────────────

        try:

            like_query = f"%{literal.lower()}%"

            

            # Agentic Specificity Check: Abort if literal is too generic

            threshold = self._get_dynamic_threshold(literal)

            count_row = conn.execute(

                "SELECT COUNT(*) FROM symbols WHERE LOWER(name) LIKE ?",

                (like_query,),

            ).fetchone()

            

            if count_row and count_row[0] > threshold:

                logger.warning(

                    f"[EvidenceLoop] ðŸ›‘ ABORT: LIKE literal '{literal}' is too generic "

                    f"({count_row[0]} > {threshold} matches). Dropping to prevent noise."

                )

                return []

            rows = conn.execute(

                "SELECT DISTINCT path, name FROM symbols "

                "WHERE LOWER(name) LIKE ? LIMIT 10",

                (like_query,),

            ).fetchall()

            return [(r[0], r[1]) for r in rows]

        except Exception:

            pass

        return []

    # =========================================================================

    # SOURCE 3: SQLITE SYMBOL LOOKUP

    # =========================================================================

    def _query_sqlite_symbols(

        self,

        symbols: List[str],

        hypothesis_id: str,

        visited: Set[str],

    ) -> List[EvidenceItem]:

        if not self.localizer.sqlite_store or not symbols:

            return []

        items: List[EvidenceItem] = []

        conn = self.localizer.sqlite_store._conn

        for sym in symbols[:8]:

            try:

                like_query = f"%{sym.lower()}%"

                

                # Agentic Specificity Check: Abort if symbol is too generic

                threshold = self._get_dynamic_threshold(sym)

                count_row = conn.execute(

                    "SELECT COUNT(*) FROM symbols WHERE LOWER(name) LIKE ?",

                    (like_query,),

                ).fetchone()

                

                if count_row and count_row[0] > threshold:

                    logger.warning(

                        f"[EvidenceLoop] ðŸ›‘ ABORT: Symbol '{sym}' is too generic "

                        f"({count_row[0]} > {threshold} matches). Dropping to prevent noise."

                    )

                    continue

                rows = conn.execute(

                    "SELECT DISTINCT path, name, kind, spring_stereotype "

                    "FROM symbols WHERE LOWER(name) LIKE ? LIMIT 10",

                    (like_query,),

                ).fetchall()

                for path, name, kind, stereotype in rows:

                    if not path or path in visited:

                        continue

                    items.append(EvidenceItem(

                        provider="literal", strength="medium", details="sqlite_symbol", file_path=path,

                        evidence_type="definition",

                        content_snippet=f"{kind}: {name}" + (f" [{stereotype}]" if stereotype else ""),

                        relevance_score=0.80,

                        hypothesis_id=hypothesis_id,

                        symbol_name=name,

                    ))

            except Exception as exc:

                logger.debug(f"Symbol query for '{sym}' failed: {exc}")

        return items

    # =========================================================================

    # SOURCE 4: NEO4J RELATIONSHIP QUERIES

    # =========================================================================

    def _query_neo4j(

        self,

        symbols: List[str],

        hypotheses: List[InvestigationHypothesis],

        visited: Set[str],

        run_ctx=None,

    ) -> List[EvidenceItem]:

        if not self.localizer.neo4j_store or not symbols:

            return []

        items: List[EvidenceItem] = []

        for sym in symbols[:5]:

            try:

                # Callers of this symbol

                result = self.localizer.neo4j_store.query(

                    """

                    MATCH (caller)-[:CALLS|DEPENDS_ON]->(target)

                    WHERE target.name = $name

                    RETURN caller.name as caller_name, caller.file_path as file_path

                    LIMIT 3

                    """,

                    {"name": sym},

                )

                for record in (result or []):

                    path = record.get("file_path")

                    if not path or path in visited:

                        continue

                    items.append(EvidenceItem(

                        provider="graph", strength="medium", details="neo4j", file_path=path,

                        evidence_type="relationship",

                        content_snippet=f"calls {sym}",

                        relationship="calls",

                        relevance_score=0.70,

                        symbol_name=record.get("caller_name"),

                    ))

            except Exception as exc:

                logger.debug(f"Neo4j query for '{sym}' failed: {exc}")

                if run_ctx:

                    from ticket_to_code.runtime.run_context import HealthLevel

                    run_ctx.degrade("neo4j", HealthLevel.FAILED, f"Neo4j query failed: {exc}")

        return items

    # =========================================================================

    # SOURCE 5: TYPESCRIPT COMPONENT RELATIONSHIP CHAINS

    # =========================================================================

    def _follow_ts_chains(

        self,

        seed_paths: List[str],

        visited: Set[str],

    ) -> List[EvidenceItem]:

        """

        Follow Angular/TypeScript structural edges:

          template_of  (component.ts → component.html)

          style_of     (component.ts → component.scss)

        Uses LocalizationAgent._graph_neighbors() which already queries

        the edges table populated by the TypeScript parser.

        """

        ts_seeds = [p for p in seed_paths if p.endswith((".ts", ".tsx"))]

        if not ts_seeds:

            return []

        items: List[EvidenceItem] = []

        neighbors = self.localizer._graph_neighbors(ts_seeds, depth=1)

        for neighbor_path, score in neighbors.items():

            if neighbor_path in visited:

                continue

            # Only follow structural TS edges

            ext = Path(neighbor_path).suffix.lower()

            if ext not in {".ts", ".tsx", ".html", ".scss", ".css"}:

                continue

            items.append(EvidenceItem(

                provider="graph", strength="medium", details="ts_chain", file_path=neighbor_path,

                evidence_type="relationship",

                content_snippet=self._read_snippet(neighbor_path),

                relationship="ts_structural",

                relevance_score=min(score, 1.0),

            ))

        logger.debug(f"  TS chain: {len(items)} new file(s) from {len(ts_seeds)} seeds")

        return items

    # =========================================================================

    # SOURCE 6: JAVA CONTROLLER / SERVICE / REPOSITORY CHAINS

    # =========================================================================

    # Regex patterns for parameter type extraction across languages

    _JAVA_PARAM_TYPE_RE = re.compile(

        r'(?:List|Set|Map|Collection|Optional|Iterable)<\s*([A-Z]\w*)\s*>'

        r'|(?:public|private|protected|\s)\s+\w[\w<>]*\s+\w+\s*\((?:[^)]*?\b([A-Z]\w+)\s+\w+)',

        re.MULTILINE,

    )

    _TS_IMPORT_TYPE_RE = re.compile(

        r"import\s*\{[^}]*\}\s*from\s*['\"](\.[^'\"]*)['\"]"

        r"|:\s*([A-Z]\w+)(?:[\[\]<>]|\s*[;,={)])",

        re.MULTILINE,

    )

    _PY_TYPE_HINT_RE = re.compile(

        r':\s*([A-Z]\w+)(?:\[|\s*[,=)])|->\s*([A-Z]\w+)',

        re.MULTILINE,

    )

    def _follow_java_chains(

        self,

        seed_paths: List[str],

        visited: Set[str],

    ) -> List[EvidenceItem]:

        """

        Follow Java/Spring architectural layer chains using the edges table.

        Traverses 'calls', 'has_type' (DI injection), 'extends', 'implements'

        edges between Java files, and also traces parameter/field types (DTOs,

        models, entities) that are not annotated with Spring stereotypes.

        """

        java_seeds = [p for p in seed_paths if p.endswith(".java")]

        if not java_seeds or not self.localizer.sqlite_store:

            return []

        items: List[EvidenceItem] = []

        conn = self.localizer.sqlite_store._conn

        # Use graph neighbors which already handle out/in/dst_name edges

        neighbors = self.localizer._graph_neighbors(java_seeds, depth=1)

        for neighbor_path, score in neighbors.items():

            if neighbor_path in visited:

                continue

            if not neighbor_path.endswith(".java"):

                continue

            try:

                stereo_row = conn.execute(

                    "SELECT spring_stereotype FROM symbols WHERE path = ? AND spring_stereotype != '' LIMIT 1",

                    (neighbor_path,),

                ).fetchone()

                stereotype = stereo_row[0] if stereo_row else None

            except Exception:

                stereotype = None

            items.append(EvidenceItem(

                provider="graph", strength="medium", details="java_chain", file_path=neighbor_path,

                evidence_type="call_chain",

                content_snippet=(

                    f"Spring stereotype: {stereotype}" if stereotype

                    else self._read_snippet(neighbor_path)

                ),

                relationship="java_layer",

                relevance_score=min(score * (1.3 if stereotype else 1.0), 1.0),

                symbol_name=stereotype,

            ))

        # Fix A: trace DTO/Model parameter types not reachable via graph edges

        seen_param_types: Set[str] = {e.file_path for e in items} | visited

        for seed in java_seeds:

            content = self._read_snippet(seed, max_chars=60000)

            if not content:

                continue

            for m in self._JAVA_PARAM_TYPE_RE.finditer(content):

                type_name = m.group(1) or m.group(2)

                if not type_name or not type_name[0].isupper():

                    continue

                try:

                    rows = conn.execute(

                        "SELECT DISTINCT path FROM symbols WHERE name = ? AND path LIKE '%.java' LIMIT 5",

                        (type_name,),

                    ).fetchall()

                    for (path,) in rows:

                        if path and path not in seen_param_types:

                            seen_param_types.add(path)

                            items.append(EvidenceItem(

                                provider="graph", strength="strong", details="java_chain",

                                file_path=path,

                                evidence_type="parameter_type",

                                content_snippet=f"DTO/Model parameter type: {type_name} used in {seed}",

                                relationship="param_type",

                                relevance_score=0.85,

                                symbol_name=type_name,

                            ))

                except Exception:

                    pass

        logger.debug(f"  Java chain: {len(items)} new file(s) from {len(java_seeds)} seeds (incl. DTO param types)")

        return items

    def _follow_python_chains(

        self,

        seed_paths: List[str],

        visited: Set[str],

    ) -> List[EvidenceItem]:

        """Trace Python type hints and dataclass/Pydantic field types."""

        py_seeds = [p for p in seed_paths if p.endswith(".py")]

        if not py_seeds or not self.localizer.sqlite_store:

            return []

        items: List[EvidenceItem] = []

        conn = self.localizer.sqlite_store._conn

        seen: Set[str] = set(visited)

        for seed in py_seeds:

            content = self._read_snippet(seed, max_chars=60000)

            if not content:

                continue

            for m in self._PY_TYPE_HINT_RE.finditer(content):

                type_name = m.group(1) or m.group(2)

                if not type_name or type_name in {"None", "Optional", "List", "Dict", "Set", "Tuple", "Any"}:

                    continue

                try:

                    rows = conn.execute(

                        "SELECT DISTINCT path FROM symbols WHERE name = ? AND path LIKE '%.py' LIMIT 5",

                        (type_name,),

                    ).fetchall()

                    for (path,) in rows:

                        if path and path not in seen:

                            seen.add(path)

                            items.append(EvidenceItem(

                                provider="graph", strength="medium", details="python_chain",

                                file_path=path,

                                evidence_type="parameter_type",

                                content_snippet=f"Python type hint: {type_name} in {seed}",

                                relationship="param_type",

                                relevance_score=0.80,

                                symbol_name=type_name,

                            ))

                except Exception:

                    pass

        logger.debug(f"  Python chain: {len(items)} new file(s) from {len(py_seeds)} seeds")

        return items

    # =========================================================================

    # SOURCE 7: CSS / SCSS OWNERSHIP CHAINS

    # =========================================================================

    def _follow_css_chains(

        self,

        seed_paths: List[str],

        visited: Set[str],

    ) -> List[EvidenceItem]:

        """

        Follow CSS/SCSS structural edges and @import chains.

        1. Uses 'style_of' edges in the SQLite edges table.

        2. Also scans @import / @use / @forward directives inside SCSS files

           to surface stylesheets imported by the seeded ones.

        """

        css_seeds = [p for p in seed_paths if Path(p).suffix.lower() in {".scss", ".css", ".sass"}]

        if not css_seeds:

            return []

        items: List[EvidenceItem] = []

        # graph_neighbors handles style_of edges

        neighbors = self.localizer._graph_neighbors(css_seeds, depth=1)

        for neighbor_path, score in neighbors.items():

            if neighbor_path in visited:

                continue

            if Path(neighbor_path).suffix.lower() not in {".scss", ".css", ".sass", ".ts", ".tsx"}:

                continue

            items.append(EvidenceItem(

                provider="graph", strength="medium", details="css_chain", file_path=neighbor_path,

                evidence_type="css_owner",

                content_snippet=self._read_snippet(neighbor_path),

                relationship="style_of",

                relevance_score=min(score, 1.0),

            ))

        # Also follow @import / @use directives inside SCSS seeds

        import_re = re.compile(r"""@(?:import|use|forward)\s+['"]([^'"]+)['"]""")

        for seed in css_seeds:

            seed_file = self._workspace / seed

            if not seed_file.exists():

                continue

            try:

                content = seed_file.read_text(encoding="utf-8", errors="ignore")

            except Exception:

                continue

            for m in import_re.finditer(content):

                raw = m.group(1)

                # Resolve relative to seed file's directory

                resolved = (seed_file.parent / raw).resolve()

                # Try with/without underscore prefix and common extensions

                for suffix in ("", ".scss", ".css"):

                    for stem in (resolved.name, "_" + resolved.name):

                        candidate = resolved.parent / (stem + suffix)

                        try:

                            rel = str(candidate.relative_to(self._workspace)).replace("\\", "/")

                        except ValueError:

                            continue

                        if rel in visited or not candidate.exists():

                            continue

                        items.append(EvidenceItem(

                            provider="graph", strength="medium", details="css_chain", file_path=rel,

                            evidence_type="css_owner",

                            content_snippet=f"@import from {seed}",

                            relationship="css_import",

                            relevance_score=0.65,

                        ))

        logger.debug(f"  CSS chain: {len(items)} new file(s) from {len(css_seeds)} seeds")

        return items

    # =========================================================================

    # SOURCE 8: CONFIGURATION / VALUE PROPAGATION CHAINS

    # =========================================================================

    def _follow_config_chains(

        self,

        literals: List[str],

        visited: Set[str],

    ) -> List[EvidenceItem]:

        """

        Search application-level config / property files for specific literal values.

        Deliberately narrow to avoid noise:

          - Only application.properties, *.yaml, *.yml, *.xml, *.env etc.

          - JSON is excluded (almost every word appears in data/i18n JSON files).

          - Literals shorter than _CONFIG_MIN_LITERAL_LEN (6) chars are skipped.

          - Directories like traces/, assets/, postman/, .idea/ are always skipped.

        """

        # Filter to only meaningful, specific literals

        lits_lower = [

            l.lower() for l in literals

            if len(l.strip()) >= _CONFIG_MIN_LITERAL_LEN

        ]

        if not lits_lower:

            return []

        items: List[EvidenceItem] = []

        _SKIP_DIRS = {

            ".git", "node_modules", "target", ".aviator",

            "dist", "build", ".angular", "__pycache__",

        }

        for root, dirs, files in self._os_walk():

            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

            for fname in files:

                fpath = Path(root) / fname

                if fpath.suffix.lower() not in _CONFIG_EXTS:

                    continue

                try:

                    rel = str(fpath.relative_to(self._workspace)).replace("\\", "/")

                except ValueError:

                    continue

                # Skip noise directories by path segment

                rel_parts = set(rel.lower().split("/"))

                if rel_parts & _CONFIG_SKIP_SEGMENTS:

                    continue

                if rel in visited:

                    continue

                try:

                    content_lower = fpath.read_text(encoding="utf-8", errors="ignore").lower()

                except Exception:

                    continue

                matched = [l for l in lits_lower if l in content_lower]

                if matched:

                    items.append(EvidenceItem(

                        provider="graph", strength="medium", details="config_chain", file_path=rel,

                        evidence_type="config_value",

                        content_snippet=f"contains literals: {matched[:3]}",

                        relevance_score=0.65,

                    ))

        logger.debug(f"  Config chain: {len(items)} config file(s) matched")

        return items

    def _os_walk(self):

        """Compatibility wrapper for os.walk since Path.walk() needs Python 3.12+."""

        import os

        for root, dirs, files in os.walk(str(self._workspace)):

            yield Path(root), dirs, files

    # =========================================================================

    # SOURCE 9: PARENT LAYOUT OWNERSHIP CHAINS

    # =========================================================================

    def _follow_layout_chains(

        self,

        seed_paths: List[str],

        visited: Set[str],

    ) -> List[EvidenceItem]:

        """

        Find parent layout containers that own the same CSS classes as seed

        SCSS files but also contain layout-critical properties.

        Reuses the same scanning logic as ownership_completeness_node, but

        expressed here as evidence rather than task expansion.

        """

        scss_seeds = [

            p for p in seed_paths

            if Path(p).suffix.lower() in {".scss", ".css", ".sass"}

        ]

        if not scss_seeds:

            return []

        items: List[EvidenceItem] = []

        _TOP_CLASS_RE = re.compile(r"^\s*\.([\w][\w-]*)\s*\{", re.MULTILINE)

        _LAYOUT_CSS_RE = re.compile(

            r"\b(display|flex|flex-direction|position|height|min-height|"

            r"max-height|width|overflow|overflow-y|overflow-x|top|bottom|"

            r"left|right)\s*:",

            re.IGNORECASE,

        )

        for seed in scss_seeds:

            seed_file = self._workspace / seed

            if not seed_file.exists():

                continue

            try:

                local_content = seed_file.read_text(encoding="utf-8", errors="ignore")

            except Exception:

                continue

            local_classes = {m.group(1) for m in _TOP_CLASS_RE.finditer(local_content)}

            if not local_classes:

                continue

            # Search the grandparent directory (module folder)

            module_dir = seed_file.parent.parent

            if not module_dir.is_dir():

                continue

            for candidate in sorted(module_dir.rglob("*.scss")):

                try:

                    rel = str(candidate.relative_to(self._workspace)).replace("\\", "/")

                except ValueError:

                    continue

                if rel == seed or rel in visited:

                    continue

                try:

                    cand_content = candidate.read_text(encoding="utf-8", errors="ignore")

                except Exception:

                    continue

                cand_classes = {m.group(1) for m in _TOP_CLASS_RE.finditer(cand_content)}

                shared = local_classes & cand_classes

                if not shared:

                    continue

                # Require at least one shared class to have layout properties

                has_layout = any(

                    bool(_LAYOUT_CSS_RE.search(self._extract_class_block(cand_content, cls)))

                    for cls in shared

                )

                if not has_layout:

                    continue

                items.append(EvidenceItem(

                    provider="graph", strength="medium", details="layout_chain", file_path=rel,

                    evidence_type="layout_owner",

                    content_snippet=f"shares layout CSS classes {list(shared)[:3]} with {seed}",

                    relationship="layout_sibling",

                    relevance_score=0.72,

                ))

        logger.debug(f"  Layout chain: {len(items)} layout owner(s) found")

        return items

    # =========================================================================

    # SOURCE 10: LIVE REPOSITORY SEARCH (Phase 3A)

    # =========================================================================

    def _query_repository_search(

        self,

        hypotheses: List[InvestigationHypothesis],

        visited: Set[str],

    ) -> List[EvidenceItem]:

        """

        Search the entire repository filesystem for literals and filename

        fragments derived from the current hypotheses.

        Unlike sources 1-9 (which depend on SQLite / Neo4j / embeddings),

        this source reads files directly — discovering any file type including

        .sh, .bat, .ps1, .json, .xml, .env, and unindexed YAML configs.

        Populated items carry source='repository_search', which the

        GroundedUnderstanding node includes in change_group when the file's

        relevance_score >= 0.70.

        """

        if not self.repo_search:

            return []

        items: List[EvidenceItem] = []

        seen_keys: Set[str] = set()   # (hypothesis_id, file_path) — dedup per hypothesis

        for hyp in hypotheses:

            # ── A. Literal content search ─────────────────────────────────────

            for literal in hyp.literals:

                if len(literal.strip()) < _REPO_SEARCH_MIN_LITERAL_LEN:

                    continue

                results = self.repo_search.search_literal(literal)

                for r in results:

                    key = f"{hyp.id}:{r.file_path}"

                    if r.file_path in visited or key in seen_keys:

                        continue

                    # Encode provenance: if the match was via an expanded term,

                    # preserve BOTH the original hypothesis literal and the

                    # matched (expanded) term so the ranking engine can credit

                    # the match against the original literal.

                    orig = r.original_literal or literal

                    if r.query != orig:

                        snippet = (

                            f"[literal:{orig!r}][expanded:{r.query!r}]"

                            f" line {r.line_number}: {r.matched_text}"

                        )

                    else:

                        snippet = (

                            f"[literal:{r.query!r}] line {r.line_number}: {r.matched_text}"

                        )

                    items.append(EvidenceItem(

                        provider="literal", strength="strong", details="repository_search", file_path=r.file_path,

                        evidence_type="usage",

                        content_snippet=snippet[:400],

                        relevance_score=min(float(r.confidence), 1.0),

                        hypothesis_id=hyp.id,

                    ))

                    seen_keys.add(key)

            # ── B. Filename / path search from hypothesis symbols ─────────────

            for sym in hyp.symbols:

                # Derive filesystem-friendly fragment: CamelCase → lowercase parts

                # e.g. "GenericConstants" → "generic" might be too lossy; use as-is

                fragment = sym.lower().replace("_", "-")

                results = self.repo_search.search_filename(fragment)

                for r in results:

                    key = f"{hyp.id}:{r.file_path}"

                    if r.file_path in visited or key in seen_keys:

                        continue

                    items.append(EvidenceItem(

                        provider="literal", strength="strong", details="repository_search", file_path=r.file_path,

                        evidence_type="definition",

                        content_snippet=f"[filename:{sym!r}] {r.matched_text}"[:400],

                        relevance_score=min(float(r.confidence), 1.0),

                        hypothesis_id=hyp.id,

                    ))

                    seen_keys.add(key)

        # Write all collected trace records to disk once per call

        self.repo_search.flush_trace()

        logger.info(

            f"  [repository_search] {len(items)} new file(s) discovered "

            f"(literal + filename searches across all {len(hypotheses)} hypotheses)"

        )

        return items

    def _extract_class_block(self, content: str, class_name: str) -> str:

        """Extract the body of a CSS class block for layout property inspection."""

        opener = re.compile(r"\." + re.escape(class_name) + r"\s*\{")

        m = opener.search(content)

        if not m:

            return ""

        pos = m.end()

        depth = 1

        chars: list = []

        while pos < len(content) and depth > 0:

            ch = content[pos]

            if ch == "{":

                depth += 1

            elif ch == "}":

                depth -= 1

                if depth == 0:

                    break

            chars.append(ch)

            pos += 1

        return "".join(chars)

    # =========================================================================

    # B6: EVIDENCE PROMOTION VALIDATION

    # =========================================================================

    # Minimum relevance score for an evidence item to be promoted.

    # Items below this are noise unless they support a specific hypothesis.

    _MIN_PROMOTION_SCORE = 0.25

    # Sources considered "weak" by themselves (must also have high score or hyp link)

    _WEAK_SOURCES = frozenset({"config_chain", "layout_chain"})

    def _validate_evidence_promotion(

        self,

        items: List[EvidenceItem],

        hypotheses: List[Any],

    ) -> List[EvidenceItem]:

        """

        B6: Filter evidence to items that meet promotion criteria.

        Promotion criteria (ANY of):

          a) relevance_score >= _MIN_PROMOTION_SCORE  AND  source not in weak-only set

          b) hypothesis_id is set (item explicitly supports a known hypothesis)

          c) source in strong set (sqlite_fts, neo4j, semantic) — regardless of score

        Items that fail all criteria are dropped as noise.

        This prevents generic keyword hits from inflating confidence while

        keeping all genuinely grounded items.

        """

        if not items:

            return items

        _hyp_ids = {

            (getattr(h, "id", None) or getattr(h, "hypothesis_id", None))

            for h in (hypotheses or [])

            if h is not None

        }

        _STRONG_SOURCES = frozenset({

            "semantic", "sqlite_fts", "sqlite_symbol",

            "neo4j", "ts_chain", "java_chain", "repository_search",

            "rkb_feature_match", "visual_chain",

        })

        validated: List[EvidenceItem] = []

        for item in items:

            score   = getattr(item, "relevance_score", 0.0)

            source  = getattr(item, "source", "")

            hyp_id  = getattr(item, "hypothesis_id", None)

            # Criterion a: strong score + not weak-only source

            if score >= self._MIN_PROMOTION_SCORE and source not in self._WEAK_SOURCES:

                validated.append(item)

                continue

            # Criterion b: explicitly supports a known hypothesis

            if hyp_id and hyp_id in _hyp_ids:

                validated.append(item)

                continue

            # Criterion c: strong source (structural evidence, always trust)

            if source in _STRONG_SOURCES:

                validated.append(item)

                continue

            logger.debug(

                "B6: dropped evidence item %s (score=%.2f source=%s hyp=%s)",

                getattr(item, "file_path", "?"), score, source, hyp_id,

            )

        return validated

    # =========================================================================

    # CONFIDENCE CALCULATION

    # =========================================================================

    def _compute_confidence(

        self,

        evidence: List[EvidenceItem],

        hypotheses: List[InvestigationHypothesis],

        localized_tasks: List[DevelopmentTask],

    ) -> float:

        """

        Compute an evidence confidence score in [0, 1].

        Formula:

          coverage  = unique_evidence_files / (localized_files + 1)  (capped at 1)

          diversity = unique_sources / max_sources

          quality   = mean(relevance_score) over all items

          depth     = log2(1 + len(evidence)) / 5  (saturates around 31 items)

          confidence = 0.30*coverage + 0.25*diversity + 0.30*quality + 0.15*depth

        """

        if not evidence:

            return 0.0

        unique_files   = len({e.file_path for e in evidence})

        unique_sources = len({e.provider for e in evidence})

        max_sources    = 10  # total possible source kinds (9 indexed + repository_search)

        localized_count = len([t for t in localized_tasks if t.task_type.value != "read_only"])

        coverage  = min(unique_files / max(localized_count + 1, 1), 1.0)

        diversity = unique_sources / max_sources

        quality   = sum(e.relevance_score for e in evidence) / len(evidence)

        import math

        depth = math.log2(1 + len(evidence)) / 5.0

        depth = min(depth, 1.0)

        confidence = (

            0.30 * coverage

            + 0.25 * diversity

            + 0.30 * quality

            + 0.15 * depth

        )

        return min(confidence, 1.0)

    # =========================================================================

    # P0: PROGRESSIVE CONTEXT — SUFFICIENCY CHECK

    # =========================================================================

    def check_sufficiency(

        self,

        ticket: ValueEdgeTicket,

        requirements: Any,

        evidence_items: List[EvidenceItem],

        hypotheses: List[Any],

        iteration: int,

    ) -> SufficiencyCheck:

        """

        Ask the LLM: 'Do I know enough to proceed?'

        Reasoning-driven gate. The LLM evaluates point-by-point:

        - What evidence do we have?

        - What is still missing?

        - Should we search more, or proceed to planning?

        Returns a SufficiencyCheck with updated search directions

        if the evidence is not yet sufficient.

        """

        from aviator.services.llm import LLMRegistry

        from ticket_to_code.llm_utils import llm_invoke

        from langchain_core.messages import HumanMessage

        import re

        # Build evidence summary (compact — don't dump everything)

        evidence_summary = []

        for e in evidence_items[:30]:  # Cap to avoid token explosion

            evidence_summary.append(

                f"- [{e.provider}] {e.file_path} (score={e.relevance_score:.2f}): {e.content_snippet[:100]}"

            )

        # Build hypothesis summary

        hyp_summary = []

        for h in (hypotheses or []):

            hyp_text = getattr(h, "hypothesis", str(h))

            hyp_summary.append(f"- {hyp_text[:150]}")

        # Build requirements summary

        req_text = ""

        if requirements:

            func_reqs = getattr(requirements, "functional_requirements", [])

            tech_reqs = getattr(requirements, "technical_requirements", [])

            req_text = "Functional Requirements:\n" + "\n".join(

                f"  - {r}" for r in func_reqs[:7]

            )

            if tech_reqs:

                req_text += "\nTechnical Requirements:\n" + "\n".join(

                    f"  - {r}" for r in tech_reqs[:5]

                )

        prompt = f"""You are an evidence sufficiency evaluator for an autonomous code modification engine.

TICKET: {ticket.title}

DESCRIPTION: {ticket.description[:500]}

{req_text}

HYPOTHESES (what we think needs to change):

{chr(10).join(hyp_summary) if hyp_summary else "  (none generated yet)"}

EVIDENCE COLLECTED (iteration {iteration}, {len(evidence_items)} items):

{chr(10).join(evidence_summary) if evidence_summary else "  (none collected yet)"}

TASK: Evaluate whether the collected evidence is SUFFICIENT to create an architectural plan.

Think in points. For each point, state:

1. What specific aspect of the ticket is covered by the evidence

2. What specific aspect is NOT yet covered

3. Whether we need to search for more evidence or can proceed

Rules:

- If we have identified the correct files AND understand what needs to change → sufficient

- If we found files but don't understand the change scope → insufficient

- If we found nothing relevant → insufficient

- If the ticket is simple (typo fix, config change) and we found the target → sufficient even with little evidence

- After iteration 3+, bias toward sufficient (diminishing returns)

Return your assessment as a JSON object matching this schema:

{{

  "is_sufficient": bool,

  "reasoning_points": ["string", "string"],

  "confidence_score": float (0.0 to 1.0),

  "evidence_score": float (0.0 to 1.0),

  "coverage_score": float (0.0 to 1.0),

  "new_search_directions": ["string", "string"] (if insufficient)

}}

ONLY output valid JSON. DO NOT wrap it in markdown block quotes."""

        try:

            llm = LLMRegistry.get_llm(assistant=False)

            response = llm_invoke(llm, [HumanMessage(content=prompt)])

            text = response.content

            match = re.search(r'\{.*\}', text, re.DOTALL)

            if match:

                text = match.group(0)

            check = SufficiencyCheck.model_validate_json(text)

            logger.info(

                f"  [SufficiencyCheck] sufficient={check.is_sufficient} "

                f"confidence={check.confidence_score:.2f} "

                f"evidence={check.evidence_score:.2f} "

                f"coverage={check.coverage_score:.2f}"

            )

            for pt in check.reasoning_points[:5]:

                logger.info(f"    â€¢ {pt}")

            return check

        except Exception as exc:

            logger.warning(f"  [SufficiencyCheck] LLM call failed: {exc} — defaulting to heuristic")

            # Heuristic fallback: use existing confidence computation

            confidence = self._compute_confidence(evidence_items, hypotheses or [], [])

            return SufficiencyCheck(

                is_sufficient=confidence >= _CONFIDENCE_THRESHOLD,

                confidence_score=confidence,

                evidence_score=confidence,

                coverage_score=confidence,

                reasoning_points=[f"Heuristic fallback: computed confidence={confidence:.3f}"],

                what_is_known=[f"Found {len(evidence_items)} evidence items"],

                what_is_missing=[] if confidence >= _CONFIDENCE_THRESHOLD else ["LLM check unavailable"],

                new_search_directions=[],

            )

    # =========================================================================

    # STAGE 0: UI TEXT LABEL EXTRACTOR

    # =========================================================================

    # Regex: Capitalized multi-word phrases (Title Case) → likely UI labels

    # Uses [ \t]+ (not \s+) to prevent matching across newlines.

    _TITLE_CASE_RE = re.compile(

        r'\b([A-Z][a-z]+(?:[ \t]+[A-Za-z][a-z]+){1,5})\b'

    )

    # Regex: Quoted strings in ticket text

    _QUOTED_RE = re.compile(r'["\']([^"\'\']{3,60})["\'\'\u201c\u201d]')

    # Regex: Visual evidence markers from Enhancement 8

    _VISUAL_MARKER_RE = re.compile(

        r'(?:Visual UI State|UI state|diagnosis|ui_state):\s*(.+)',

        re.IGNORECASE,

    )

    # Common English words that are NOT UI labels (noise filter)

    _STOP_LABELS = frozenset({

        "the", "and", "for", "not", "but", "with", "from", "this", "that",

        "have", "has", "are", "was", "were", "will", "can", "may", "should",

        "make", "full", "starting", "end", "code", "bug", "fix", "error",

        "issue", "problem", "please", "need", "want", "like", "also",

        "just", "only", "more", "less", "very", "some", "all", "any",

        "new", "old", "first", "last", "next", "back", "add", "edit",

        "delete", "save", "cancel", "close", "open", "click", "button",

        "page", "screen", "tab", "modal", "dialog", "form", "input",

        "label", "text", "value", "data", "list", "item", "option",

        "image", "file", "see", "show", "hide", "visible", "hidden",

    })

    def _extract_ui_text_labels(

        self,

        ticket: "ValueEdgeTicket",

        hypotheses: Optional[List[Any]] = None,

    ) -> List[str]:

        """

        Stage 0: Extract actionable UI text labels from ALL input sources.

        Sources (in priority order):

          1. Visual evidence section injected by Enhancement 8

          2. Ticket title and description

          3. Hypothesis text

        Returns labels that are likely UI-visible text (page titles, tab names,

        button labels, section headings) suitable for i18n chain tracing.

        Zero LLM tokens — pure regex extraction.

        """

        candidates: List[str] = []

        title = getattr(ticket, "title", "") or ""

        desc = getattr(ticket, "description", "") or ""

        full_text = f"{title}\n{desc}"

        # ── Source 1: Visual Evidence section ──────────────────────────────

        for m in self._VISUAL_MARKER_RE.finditer(full_text):

            visual_text = m.group(1).strip()

            # Split on sentence-ending periods, commas, semicolons, pipes, bullets

            for segment in re.split(r'[.!,;|â€¢]+', visual_text):

                seg = segment.strip()

                if len(seg) >= 3 and seg.lower() not in self._STOP_LABELS:

                    # If still too long, try to extract quoted substrings first

                    if len(seg) > 60:

                        # Pull out short quoted phrases from the long segment

                        inner_quotes = re.findall(r"['\"]([^'\"]{3,50})['\"]", seg)

                        if inner_quotes:

                            candidates.extend(inner_quotes)

                        # Also extract camelCase/PascalCase identifiers

                        identifiers = re.findall(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b', seg)

                        candidates.extend(identifiers)

                        # Skip the raw long sentence — it's not grepable

                        continue

                    candidates.append(seg)

        # ── Source 2: Quoted strings in ticket ────────────────────────────

        for m in self._QUOTED_RE.finditer(full_text):

            q = m.group(1).strip()

            if len(q) >= 3:

                candidates.append(q)

        # ── Source 3: Title Case phrases (multi-word proper nouns) ────────

        for m in self._TITLE_CASE_RE.finditer(full_text):

            phrase = m.group(1).strip()

            # Reject if it spans a newline (defensive — regex shouldn't match,

            # but guard against edge cases in formatted ticket text).

            if '\n' in phrase or '\r' in phrase:

                continue

            words = phrase.split()

            # Must have at least 2 words and at least 1 substantive non-stop

            # word (>= 3 chars).  Filters out "In the", "Is not", etc.

            if len(words) >= 2:

                non_stop = [

                    w for w in words

                    if w.lower() not in self._STOP_LABELS and len(w) >= 3

                ]

                if non_stop:

                    candidates.append(phrase)

        # ── Source 4: Hypothesis anchors ──────────────────────────────────

        for h in (hypotheses or []):

            for anchor in getattr(h, "anchors", []):

                if anchor and len(anchor) >= 3:

                    candidates.append(anchor)

        # ── Source 5: Code-like identifiers from ticket text ──────────────

        # Extract camelCase, PascalCase, snake_case identifiers from ticket

        # text that look like class/method/variable names

        code_idents = re.findall(

            r'\b([a-z][a-zA-Z0-9]*(?:[A-Z][a-z0-9]+)+)\b',  # camelCase

            full_text,

        )

        code_idents += re.findall(

            r'\b([A-Z][a-z]+(?:[A-Z][a-z]+){1,})\b',  # PascalCase

            full_text,

        )

        code_idents += re.findall(

            r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+){1,})\b',  # snake_case

            full_text,

        )

        for ident in code_idents:

            if len(ident) >= 4 and ident.lower() not in self._STOP_LABELS:

                candidates.append(ident)

        # ── Source 6: Error messages from ticket text ─────────────────────

        # Error messages like "Name has already been used" are the MOST

        # powerful i18n search anchors because they appear VERBATIM in code.

        # Extract sentences that look like error/validation messages.

        error_patterns = re.findall(

            r'(?:error|message|shows?|displays?|says?|appears?)[:\s]+["\']?([A-Z][^.!?\n]{8,80})[.!?\n"\']',

            full_text, re.IGNORECASE,

        )

        for ep in error_patterns:

            cleaned = ep.strip().strip("'\"")

            if len(cleaned) >= 8 and cleaned.lower() not in self._STOP_LABELS:

                # Error messages get highest priority — insert at front

                candidates.insert(0, cleaned)

        # Also extract any sentence containing "already" / "cannot" / "unable"

        # / "not allowed" which are almost always validation error messages

        validation_phrases = re.findall(

            r'["\']([^"\']{8,60}(?:already|cannot|unable|not allowed|must be|is required|invalid)[^"\']{0,40})["\']',

            full_text, re.IGNORECASE,

        )

        for vp in validation_phrases:

            cleaned = vp.strip()

            if len(cleaned) >= 8:

                candidates.insert(0, cleaned)

        # ── Deduplicate & filter ──────────────────────────────────────────

        seen: set = set()

        unique: List[str] = []

        for c in candidates:

            cleaned = c.strip()

            # Reject any label containing newlines (multi-line artefact)

            if '\n' in cleaned or '\r' in cleaned:

                continue

            # Reject labels that are too long to be useful grep terms

            if len(cleaned) > 60:

                continue

            key = cleaned.lower()

            if key in seen or len(key) < 3:

                continue

            # Skip pure stop-word labels

            if key in self._STOP_LABELS:

                continue

            seen.add(key)

            unique.append(cleaned)

        # Cap at 15 labels (increased to account for error messages)

        return unique[:15]

    # =========================================================================

    # STAGE 0: i18n TRANSLATION CHAIN TRACER

    # =========================================================================

    # Regex: Extract JSON key from a line like '"add.new.contract.picklists": "Pick lists"'

    _JSON_KV_RE = re.compile(

        r'["\']([\w.\-]+)["\']\s*:\s*["\']([^"\'\']+)["\'\']'

    )

    # Regex: Extract properties key from 'key=value' or 'key: value' lines

    _PROPS_KV_RE = re.compile(

        r'^\s*([\w.\-]+)\s*[=:]\s*(.+)$', re.MULTILINE

    )

    # Regex: Extract export name from 'export const XXX' or 'export class XXX'

    _EXPORT_RE = re.compile(

        r'export\s+(?:const|class|function|enum|interface|type)\s+(\w+)'

    )

    def _trace_i18n_chain(

        self,

        ui_label: str,

        visited: set,

    ) -> List[EvidenceItem]:

        """

        Agentic iterative chain search: trace UI text → i18n key → component.

        Supports multiple frameworks:

          Angular:  en.json   → {{ 'key' | translate }}  → component.ts/.html

          React:    en.json   → t('key') / intl.formatMessage  → component.tsx

          Java:     messages.properties  → getMessage("key")  → Service.java

        This is an AGENTIC search — each step's output feeds the next step's input.

        Unlike one-shot vector search, this GUARANTEES finding the component

        that renders a given UI label.

        Zero LLM tokens — pure RepositorySearchEngine (live grep) + regex.

        """

        if not self.repo_search or len(ui_label.strip()) < 3:

            return []

        items: List[EvidenceItem] = []

        traced_files: set = set(visited)

        # ── Step 1: Search i18n JSON files (Angular/React) ────────────────
        # First try the full label. If that returns 0 hits, try progressively
        # shorter suffixes — many i18n values are suffixes like " has already
        # been used" that get concatenated with a dynamic prefix at runtime,
        # so the full user-visible text never appears in any file.

        json_hits = self.repo_search.search_literal(
            ui_label,
            extensions={".json"},
            max_results=15,
        )

        # Fallback: try shorter substrings of the label
        if not json_hits:
            label_words = ui_label.strip().split()
            fallback_queries = []

            # Try suffix substrings (last N words), longest first
            for start in range(1, len(label_words)):
                suffix = " ".join(label_words[start:])
                if len(suffix) >= 10:  # skip very short fragments
                    fallback_queries.append(suffix)

            # Also try the distinctive middle portion (skip first and last word)
            if len(label_words) > 3:
                middle = " ".join(label_words[1:-1])
                if len(middle) >= 10 and middle not in fallback_queries:
                    fallback_queries.append(middle)

            for fallback_q in fallback_queries[:3]:  # try max 3 substrings
                json_hits = self.repo_search.search_literal(
                    fallback_q,
                    extensions={".json"},
                    max_results=15,
                )
                if json_hits:
                    logger.info(
                        f"    [i18n] Full label '{ui_label}' had 0 hits; "
                        f"substring '{fallback_q}' found {len(json_hits)} hit(s)"
                    )
                    break

        i18n_keys_found: List[str] = []

        for hit in json_hits:

            fp = hit.file_path

            # Only target i18n/locale/assets directories (avoid package.json noise)

            fp_lower = fp.lower().replace("\\", "/")

            is_i18n = any(

                seg in fp_lower

                for seg in ("/i18n/", "/locale", "/assets/", "/translations/", "/lang/")

            )

            if not is_i18n:

                continue

            # Extract the translation key from the matched line

            for m in self._JSON_KV_RE.finditer(hit.matched_text):

                key, value = m.group(1), m.group(2)

                # Verify the value matches our UI label (bidirectional:

                # label-in-value OR value-in-label, because i18n values

                # can be suffixes e.g. " has already been used" where the

                # full error is assembled at runtime with a prefix)

                lbl_low = ui_label.lower().strip()

                val_low = value.lower().strip()

                if lbl_low in val_low or val_low in lbl_low:

                    i18n_keys_found.append(key)

                    logger.debug(

                        f"    [i18n] Found key '{key}' = '{value}' "

                        f"in {fp}"

                    )

        # ── Step 1b: Search .properties files (Java/Spring) ───────────────

        props_hits = self.repo_search.search_literal(

            ui_label,

            extensions={".properties"},

            max_results=10,

        )

        for hit in props_hits:

            for m in self._PROPS_KV_RE.finditer(hit.matched_text):

                key, value = m.group(1), m.group(2).strip()

                lbl_low = ui_label.lower().strip()

                val_low = value.lower().strip()

                if lbl_low in val_low or val_low in lbl_low:

                    i18n_keys_found.append(key)

                    logger.debug(

                        f"    [i18n] Found properties key '{key}' = '{value}' "

                        f"in {hit.file_path}"

                    )

        if not i18n_keys_found:

            logger.debug(f"    [i18n] No translation keys found for '{ui_label}'")

            # ── DIRECT TEXT SEARCH FALLBACK ────────────────────────────────

            # Many strings are hardcoded (not in i18n files). Search for the

            # raw UI label text directly in HTML/TS/Java source files.

            # This catches error messages like "Name has already been used"

            # that appear verbatim in component templates or validators.

            if len(ui_label) >= 8 and self.repo_search:

                direct_hits = self.repo_search.search_literal(

                    ui_label,

                    extensions={".ts", ".html", ".java", ".tsx", ".jsx"},

                    max_results=10,

                )

                for dh in direct_hits:

                    if dh.file_path in traced_files:

                        continue

                    # Skip test files

                    if ".spec." in dh.file_path or "/test/" in dh.file_path.lower():

                        continue

                    traced_files.add(dh.file_path)

                    items.append(EvidenceItem(

                        provider="visual_chain",

                        strength="strong",

                        details=(

                            f"direct_text: '{ui_label}' found in {dh.file_path}"

                        ),

                        file_path=dh.file_path,

                        evidence_type="usage",

                        content_snippet=(

                            f"[direct_text:{ui_label!r}] line {dh.line_number}: "

                            f"{dh.matched_text[:200]}"

                        ),

                        relevance_score=0.88,  # High — direct text match

                        hypothesis_id="i18n_chain",

                    ))

                if items:

                    logger.info(

                        f"    [i18n] Direct text fallback for '{ui_label}': "

                        f"{len(items)} file(s) found"

                    )

            return items

        # ── Step 2: Trace each key to the component that uses it ──────────

        for key in i18n_keys_found[:5]:  # Cap to prevent explosion

            key_hits = self.repo_search.search_literal(

                key,

                extensions={".ts", ".tsx", ".html", ".java", ".jsx"},

                max_results=10,

            )

            for key_hit in key_hits:

                kfp = key_hit.file_path

                if kfp in traced_files:

                    continue

                # Skip i18n JSON files themselves (we want the COMPONENT)

                if kfp.endswith(".json"):

                    continue

                traced_files.add(kfp)

                items.append(EvidenceItem(

                    provider="visual_chain",

                    strength="strong",

                    details=(

                        f"i18n_chain: '{ui_label}' → key '{key}' → {kfp}"

                    ),

                    file_path=kfp,

                    evidence_type="usage",

                    content_snippet=(

                        f"[i18n:{key!r}] line {key_hit.line_number}: "

                        f"{key_hit.matched_text[:200]}"

                    ),

                    relevance_score=0.92,  # Very high — direct chain

                    hypothesis_id="i18n_chain",

                ))

                # ── Step 3: If it's a model/constants file (not a component),

                #    trace its export to the component that imports it ─────

                if (

                    kfp.endswith(".ts")

                    and not kfp.endswith(".component.ts")

                    and not kfp.endswith(".spec.ts")

                ):

                    export_name = self._extract_export_name(kfp)

                    if export_name:

                        usage_hits = self.repo_search.search_literal(

                            export_name,

                            extensions={".ts", ".tsx"},

                            max_results=8,

                        )

                        for usage in usage_hits:

                            if usage.file_path in traced_files:

                                continue

                            traced_files.add(usage.file_path)

                            items.append(EvidenceItem(

                                provider="visual_chain",

                                strength="strong",

                                details=(

                                    f"i18n_usage: '{export_name}' "

                                    f"imported in {usage.file_path}"

                                ),

                                file_path=usage.file_path,

                                evidence_type="usage",

                                content_snippet=(

                                    f"[import:{export_name!r}] "

                                    f"line {usage.line_number}: "

                                    f"{usage.matched_text[:200]}"

                                ),

                                relevance_score=0.90,

                                hypothesis_id="i18n_chain",

                            ))

        logger.info(

            f"    [i18n] Chain trace for '{ui_label}': "

            f"{len(i18n_keys_found)} key(s) → {len(items)} component(s)"

        )

        return items

    def _extract_export_name(self, rel_path: str) -> Optional[str]:

        """

        Read a .ts file and extract the first 'export const/class/function' name.

        Used to trace model files → their importing components.

        """

        try:

            full = self._workspace / rel_path

            if not full.exists():

                return None

            content = full.read_text(encoding="utf-8", errors="ignore")[:5000]

            m = self._EXPORT_RE.search(content)

            return m.group(1) if m else None

        except Exception:

            return None

    # =========================================================================

    # UTILITY

    # =========================================================================

    def _read_snippet(self, rel_path: str, max_chars: int = 300) -> str:

        """Read a short snippet from a file for the evidence content_snippet."""

        try:

            full = self._workspace / rel_path

            if full.exists():

                return full.read_text(encoding="utf-8", errors="ignore")[:max_chars]

        except Exception:

            pass

        return ""

