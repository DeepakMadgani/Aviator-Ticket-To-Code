import os
import re
import json
import logging
from typing import Tuple, List, Any, Optional
from pathlib import Path
from ticket_to_code.models import TaskType
from aviator.services.llm import LLMRegistry

logger = logging.getLogger(__name__)
# ----------------------------------------------------------------------
# ARCHITECTURE NOTE: Plan Gating Pipeline
# We replaced mathematical gates with Contextual LLM Judgment + Mechanical Grounding Post-Checks:
# Pass 1.5: Module Boundary Enforcement (Before LLM). Rejects tasks that violate architecture dependency rules.
# Pass 2/3: Contextual LLM Judgment + Mechanical Grounding Post-Checks:
# 1. Text Match: Ensures if the LLM says "this file has a hardcoded string X", we normalize and verify it exists.
# 2. Graph Match: Ensures if the LLM cites architectural relevance, the graph provider actually found that edge.
# 3. Implicit Structural: Ensures new files with an architectural need (no string to replace) are linked to a real consumer with a mechanically verified structural marker (e.g. "@RestController").
# 4. CREATE Consumer Check: Ensures proposed new files are structurally demanded by the content of accepted consumers.
# 5. Rationale Coherence: Ensures files are judged against the Planner's Stated Strategy, preventing hallucinations.
# Pass 2.5: Deep architectural scan for forbidden imports/references based on module_graph boundaries.
#
# Known Limitations:
# - Reject-Dependency Check (_task_references_path): Because we cannot inspect the *proposed* code of a MODIFY
#   task before it is generated, we must rely on regex word-boundary searches over the planner's `description`
#   and `explicit_planner_justification` to detect if an accepted task references a rejected CREATE task.
#   This means the check will ONLY fire if the LLM happened to explicitly name the exact filename in its prose.
#   If it writes generically ("this file needs updating"), the dependency will be silently missed. This is an
#   accepted, bounded gap for now.
# - DELETE/RENAME tasks are currently routed through the same LLM judgment prompt as MODIFY, which asks about
#   "modifying" the file. This is semantically imprecise for deletions and is a known follow-up item, not
#   fixed here.
# ----------------------------------------------------------------------


def _is_blocked_path(path: str) -> bool:
    low = path.lower().replace("\\", "/")
    if "package-lock.json" in low or "yarn.lock" in low or "pnpm-lock.yaml" in low: return True
    if "/dist/" in low or "/build/" in low or "/out/" in low: return True
    if "/node_modules/" in low or "/.git/" in low: return True
    if low.endswith(".svg") or low.endswith(".png") or low.endswith(".jpg"): return True
    return False

def passes_mechanical_prefilter(task, ranked_files, discovered_files, workspace_path: str = None) -> Tuple[bool, dict]:
    task_type = getattr(task.task_type, "value", str(task.task_type)).lower()
    if task_type == "create":
        return True, {"score": 1.0}

    norm = task.file_path.replace("\\", "/").lower()
    ranked = next((r for r in ranked_files if getattr(r, "file_path", "").replace("\\", "/").lower() == norm), None)

    score = 0.0
    if ranked:
        score = float(getattr(ranked, "final_score", 0.0))
    else:
        cand = next((d for d in discovered_files if d.get("path", "").replace("\\", "/").lower() == norm), None)
        if cand:
            score = float(cand.get("confidence", 0.0))

    if score < 0.05:
        if workspace_path:
            workspace_root = Path(workspace_path).resolve()
            candidate = Path(task.file_path)
            full_path = candidate if candidate.is_absolute() else (workspace_root / candidate)
            try:
                resolved = full_path.resolve()
                if resolved.is_file() and (resolved == workspace_root or workspace_root in resolved.parents):
                    return True, {"score": 1.0, "reason": "File exists on disk inside workspace (100% grounded)"}
            except Exception:
                pass
        return False, {"score": score, "reason": "File is completely ungrounded (score < 0.05)"}

    return True, {"score": score}

def normalize_text(text: str) -> str:
    return re.sub(r'[\s\'"`]', '', text).lower()

def _extract_json_payload(text: str) -> str:
    """Strip Markdown code fences (```json ... ``` or plain ``` ... ```) around a JSON
    payload without corrupting the payload itself.

    NOTE: do NOT use str.strip("```json") for this — str.strip(chars) removes any of the
    *individual characters* in the argument from each end of the string, not the literal
    substring "```json". That silently eats legitimate trailing characters (e.g. a payload
    ending in the letter 'n' or 'o') and is not safe for arbitrary LLM JSON output.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r'^```[a-zA-Z]*\s*', '', stripped)
        stripped = re.sub(r'```\s*$', '', stripped)
    return stripped.strip()

def verify_grounding_fact_text(fact: str, file_path: str, workspace_path: str, symbols: Optional[List[str]] = None) -> bool:
    try:
        full_path = os.path.join(workspace_path, file_path)
        if not os.path.exists(full_path):
            return False
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        norm_content = normalize_text(content)
        if normalize_text(fact) in norm_content:
            return True
        # Symbol-level grounding backstop:
        # If fact was slightly paraphrased, check if any referenced symbol exists in file
        if symbols:
            generic_symbols = {
                "class", "interface", "service", "controller", "component",
                "function", "method", "string", "list", "object", "model",
            }
            for sym in symbols:
                clean_sym = sym.split("::")[-1].split(".")[-1].strip()
                if not clean_sym:
                    continue
                low_sym = clean_sym.lower()
                if len(clean_sym) < 4 or low_sym in generic_symbols:
                    continue
                # Require a token-level hit to avoid loose substring acceptance.
                if re.search(rf'(?<![A-Za-z0-9_]){re.escape(clean_sym)}(?![A-Za-z0-9_])', content, re.IGNORECASE):
                    return True
        # File stem / class name backstop (e.g. AddMembersComponent in add-members.component.ts)
        # Keep this strict so it only applies to component-style files with class markers.
        low_path = file_path.replace("\\", "/").lower()
        is_component_file = ".component." in low_path
        has_class_marker = ("export class " in content) or ("class " in content)
        stem = Path(file_path).stem.replace("-", "").replace("_", "").lower()
        if is_component_file and has_class_marker and stem and len(stem) > 5 and stem in norm_content:
            return True
        return False
    except Exception as e:
        logger.error(f"Error reading file {file_path} for text verification: {e}")
        return False

def _task_is_additive(task) -> bool:
    """True when the task is adding new code to an existing file (not editing existing code)."""
    desc = (getattr(task, "description", "") or "").lower()
    return any(kw in desc for kw in ("add ", "implement ", "create ", "introduce ", "expose ", "insert ", "add new"))


def _primary_type_in_file(file_path: str, workspace_path: str) -> bool:
    """True when the primary type name derived from the file path stem exists as a token in the file.
    e.g. ParticipatingMembersRepository.java → looks for token 'ParticipatingMembersRepository'.
    This is the correct grounding proof for additive tasks: the class/interface that will
    house the new method must already exist in this file."""
    try:
        stem = Path(file_path).stem  # e.g. ParticipatingMembersRepository or add-members.component
        # Convert kebab/snake to PascalCase: add-members.component → AddMembersComponent
        type_name = ''.join(w.capitalize() for w in re.split(r'[-_.]', stem))
        if len(type_name) < 4:
            return False
        full_path = os.path.join(workspace_path, file_path) if workspace_path else file_path
        if not os.path.exists(full_path):
            return False
        content = Path(full_path).read_text(encoding='utf-8', errors='ignore')
        return bool(re.search(rf'(?<![A-Za-z0-9_]){re.escape(type_name)}(?![A-Za-z0-9_])', content))
    except Exception:
        return False


def verify_grounding_fact_graph(fact: str, file_path: str, evidence_items) -> bool:
    norm_fact = normalize_text(fact)
    norm_file = file_path.replace("\\", "/").lower()

    for item in evidence_items:
        item_path = getattr(item, "file_path", "").replace("\\", "/").lower()
        if item_path == norm_file or norm_file in item_path:
            snippet = getattr(item, "content_snippet", "")
            details = getattr(item, "details", "")
            combined = snippet + " " + details
            if combined and norm_fact in normalize_text(combined):
                return True
    return False

def verify_grounding_fact_structural(fact: str, file_path: str, workspace_path: str) -> bool:
    """Verifies an 'implicit_structural' grounding fact: a real structural marker that
    must genuinely exist in the consumer file. Used when a CREATE task's need is
    architectural (e.g. a new endpoint on an existing controller) rather than replacing
    an existing literal."""
    try:
        full_path = os.path.join(workspace_path, file_path)
        if not os.path.exists(full_path):
            return False
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        fact_clean = fact.strip()
        if not fact_clean or fact_clean not in content:
            return False

        valid_prefixes = (
            "@", "class ", "interface ", "extends ", "implements ",
            "export ", "export class", "function ", "public class", "public interface",
        )
        is_structural = any(fact_clean.startswith(prefix) for prefix in valid_prefixes)

        if not is_structural:
            return False

        return True
    except Exception as e:
        logger.error(f"Error reading file {file_path} for structural verification: {e}")
        return False

def contextual_llm_judgment_modify(task, ticket_text, rationale, workspace_path, evidence_items, score_dict) -> Tuple[bool, str, str, str, list]:
    llm = LLMRegistry.get_llm(assistant=False)

    file_content = ""
    try:
        full_path = os.path.join(workspace_path, task.file_path)
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                file_content = f.read()[:4000]
    except Exception:
        pass

    norm_file = task.file_path.replace("\\", "/").lower()
    relevant_evidence = []
    for item in evidence_items:
        item_path = getattr(item, "file_path", "").replace("\\", "/").lower()
        if item_path == norm_file or norm_file in item_path:
            relevant_evidence.append({
                "provider": getattr(item, "provider", ""),
                "details": getattr(item, "details", ""),
                "snippet": getattr(item, "content_snippet", "")
            })

    evidence_str = "No graph/symbol evidence found for this file."
    if relevant_evidence:
        evidence_str = json.dumps(relevant_evidence, indent=2)

    prompt = f"""You are mechanically gating a proposed code modification.

TICKET:
{ticket_text}

PLAN RATIONALE (The overall strategy you must enforce):
{rationale}

PROPOSED TASK:
File: {task.file_path}
Description: {task.description}

GRAPH / SYMBOL EVIDENCE FOR THIS FILE:
```json
{evidence_str}
```

FILE CONTENT (Start):
```
{file_content}
```

YOUR TASK:
Does modifying this file strictly support the stated PLAN RATIONALE?
If yes, provide a grounding fact that proves this file is the RIGHT place for this change.

⚠️  CRITICAL RULE: Your grounding_fact MUST be text that ALREADY EXISTS in the FILE CONTENT shown above.
Do NOT cite the new method/field/code you intend to add — cite the existing class declaration,
existing method signature, existing import, or existing interface that makes this file the correct
place to apply the change. For example, if adding a new method to a class, cite the existing
class declaration line (e.g., 'public class ParticipatingMemberService') as your grounding_fact.

- If the file contains a relevant existing declaration, import, or interface, set grounding_type="text" and provide the EXACT literal text found in the current FILE CONTENT as your grounding_fact. Do not paraphrase. If the literal text is encoded (e.g., `260300` representing version `26.3`), cite the exact encoded string.
- If the file is relevant due to data-flow/runtime architecture without a literal trace, set grounding_type="graph" and explain the relationship based EXACTLY on what is provided in the GRAPH / SYMBOL EVIDENCE section.

Output JSON only:
{{
  "verdict": "ACCEPT" | "REJECT",
  "grounding_type": "text" | "graph",
  "grounding_fact": "Exact snippet from file OR exact detail from graph evidence",
  "reasoning": "Brief explanation",
  "referenced_code_symbols": ["List of any specific classes, files, or packages you explicitly rely on in your reasoning"]
}}
"""
    try:
        response_text = _extract_json_payload(llm.invoke(prompt).content)
        llm_result = json.loads(response_text)
        if "verdict" not in llm_result:
            logger.info(f"[Contextual LLM Judgment] {task.file_path} => Error: LLM returned malformed JSON: {response_text}")
            llm_result["verdict"] = "REJECT"
            llm_result["reasoning"] = "Malformed LLM response"

        verdict = llm_result.get("verdict")
        g_fact = llm_result.get("grounding_fact", "None")
        g_type = llm_result.get("grounding_type", "text")
        logger.info(f"[Contextual LLM Judgment] {task.file_path} => {verdict} (Fact: {g_fact}) (Symbols: {llm_result.get('referenced_code_symbols')})")
        logger.info(f"LLM FULL RESULT: {llm_result}")
        reason = llm_result.get("reasoning", "")
        symbols = llm_result.get("referenced_code_symbols") or []
        if verdict == "ACCEPT":
            if g_type == "text":
                # For additive tasks (add new method/field to existing class), the correct
                # grounding proof is that the TARGET CLASS EXISTS in the file — not that
                # the new code being added exists (it doesn't yet, that's why we're adding it).
                if _task_is_additive(task) and _primary_type_in_file(task.file_path, workspace_path):
                    logger.info(f"[Gating] {task.file_path} => additive task, primary type verified in file — ACCEPT")
                    return True, reason, g_type, g_fact, symbols
                if not verify_grounding_fact_text(g_fact, task.file_path, workspace_path, symbols=symbols):
                    return False, "REJECTED: Mechanical Post-Check Failed (Hallucinated text evidence)", g_type, g_fact, symbols
            elif g_type == "graph":
                if not verify_grounding_fact_graph(g_fact, task.file_path, evidence_items):
                    return False, "REJECTED: Mechanical Post-Check Failed (Hallucinated graph evidence)", g_type, g_fact, symbols

            return True, reason, g_type, g_fact, symbols
        return False, reason, g_type, g_fact, symbols
    except Exception as e:
        logger.error(f"LLM Judgment failed for {task.file_path}: {e}")
        return False, f"LLM Error: {str(e)}", "none", "", []

def contextual_llm_judgment_create(task, ticket_text, rationale, workspace_path, accepted_modify_tasks) -> Tuple[bool, str, str, str, list]:
    llm = LLMRegistry.get_llm(assistant=False)

    consumers_str = "\n".join([f"- {t.file_path}: {t.description}" for t in accepted_modify_tasks])
    planner_justification = getattr(task, "explicit_planner_justification", None) or "(none provided)"

    prompt = f"""You are mechanically gating a proposed new file creation.

TICKET:
{ticket_text}

PLAN RATIONALE:
{rationale}

PROPOSED CREATE TASK:
New File: {task.file_path}
Description: {task.description}
Planner Justification: {planner_justification}

ACCEPTED CONSUMER TASKS:
{consumers_str}

YOUR TASK:
Does creating this file strictly support the stated PLAN RATIONALE?
There are two acceptable valid cases for file creation:
CASE A (Code File): The new file is imported or consumed by other files being modified. You MUST name the specific consumers and provide grounding evidence from the consumer file.
- If replacing existing code, set "grounding_type" to "text" and provide the exact existing snippet from the consumer file as "evidence_in_consumer".
- If the architectural need is implicit, set "grounding_type" to "implicit_structural" and provide a structural marker (e.g. "@RestController" or "class") as "evidence_in_consumer".

CASE B (Standalone / New Feature File): The new file is a configuration file (e.g., docker-compose.yml), a documentation file, OR a completely new code file for a standalone feature/enhancement that is explicitly required by the TICKET and has no consumers yet. These do NOT need a code consumer. Set "is_standalone" to true, set "consumers" to an empty list [], and provide a very strong justification in "reasoning" for why the ticket absolutely mandates the creation of this specific standalone file.

#

Output JSON only:
{{
  "verdict": "ACCEPT" | "REJECT",
  "is_standalone": true | false,
  "consumers": [
    {{
      "file_path": "path to accepted modify task",
      "grounding_type": "text" | "implicit_structural",
      "evidence_in_consumer": "Exact snippet or structural marker from consumer"
    }}
  ],
  "reasoning": "Brief explanation of how this strict creation supports the rationale, or why it qualifies as a standalone/new feature file",
  "referenced_code_symbols": ["List of any specific classes, files, or packages you explicitly rely on in your reasoning"]
}}
"""
    try:
        response = _extract_json_payload(llm.invoke(prompt).content)
        res = json.loads(response)
        verdict = res.get("verdict", "REJECT")
        is_standalone = res.get("is_standalone", False)
        consumers = res.get("consumers") or []
        reason = res.get("reasoning", "")
        symbols = res.get("referenced_code_symbols") or []

        if verdict == "ACCEPT":
            accepted_paths = [t.file_path for t in accepted_modify_tasks]
            if not is_standalone and not consumers:
                return False, "REJECTED: No consumers cited for CREATE task (and not flagged as standalone).", "create_linkage", "", symbols

            for consumer in consumers:
                c_path = consumer.get("file_path", "")
                c_ev = consumer.get("evidence_in_consumer", "")
                g_type = consumer.get("grounding_type", "text")
                if c_path not in accepted_paths:
                    return False, f"REJECTED: Consumer {c_path} is not in the accepted list.", "create_linkage", c_ev, symbols

                if g_type == "implicit_structural":
                    if not verify_grounding_fact_structural(c_ev, c_path, workspace_path):
                        return False, f"REJECTED: Mechanical Structural Check Failed (Marker '{c_ev}' not found or invalid in consumer {c_path})", "create_linkage", c_ev, symbols
                else:
                    if not verify_grounding_fact_text(c_ev, c_path, workspace_path):
                        return False, f"REJECTED: Mechanical Post-Check Failed (Evidence {c_ev} not found in consumer {c_path})", "create_linkage", c_ev, symbols

            return True, reason, "create_linkage", str(consumers) if consumers else "standalone_feature", symbols
        return False, reason, "none", "", symbols
    except Exception as e:
        logger.error(f"LLM Judgment failed for {task.file_path}: {e}")
        return False, f"LLM Error: {str(e)}", "none", "", []

def _task_references_path(task, referenced_task, workspace_path: str = None) -> bool:
    ref_path = referenced_task.file_path.replace("\\", "/")
    ref_basename = os.path.basename(ref_path)

    text_to_search = ""
    if hasattr(task, "description") and task.description:
        text_to_search += task.description + " "
    if hasattr(task, "explicit_planner_justification") and task.explicit_planner_justification:
        text_to_search += task.explicit_planner_justification

    if not text_to_search:
        return False

    text_to_search = text_to_search.lower()

    # 1. Exact full path match (no word boundary needed if path is specific)
    if ref_path.lower() in text_to_search:
        return True

    # 2. Exact basename match with regex word boundary
    # This prevents matching 'version' inside 'conversion' while allowing 'version.ts'
    pattern_basename = r'\b' + re.escape(ref_basename.lower()) + r'\b'
    if re.search(pattern_basename, text_to_search):
        return True

    return False

def validate_plan_consistency(accepted_tasks, rejected_tasks, plan, workspace_path: str = None) -> Tuple[bool, str, List[Any]]:
    if not accepted_tasks:
        return False, "All proposed tasks were rejected by gating logic.", rejected_tasks

    # Fail closed: dropping any write-intent task makes the plan incomplete.
    rejected_write_tasks = []
    for task in rejected_tasks:
        t_type = getattr(task.task_type, "value", str(task.task_type)).lower()
        if t_type in {"modify", "delete", "refactor"}:
            rejected_write_tasks.append(task)

    if rejected_write_tasks:
        return (
            False,
            "Plan is inconsistent: write-intent tasks were rejected by gating; forcing replanning.",
            rejected_write_tasks,
        )

    for task in accepted_tasks:
        for r_task in rejected_tasks:
            if _task_references_path(task, r_task, workspace_path):
                reason = f"Task {task.file_path} depends on rejected task {r_task.file_path}. "
                if getattr(r_task.task_type, "value", str(r_task.task_type)) == "create":
                    reason += f"If {r_task.file_path} is legitimately required, you must explicitly name the consumer {task.file_path} and extract the grounding fact from it."
                return False, reason, [r_task]

    deleted_paths = {t.file_path for t in accepted_tasks if getattr(t.task_type, "value", str(t.task_type)) == "delete"}
    for task in accepted_tasks:
        for d_path in deleted_paths:
            if task.file_path == d_path:
                continue  # don't check a delete task against its own deletion
            if _task_references_path(task, next((t for t in accepted_tasks if t.file_path == d_path), task), workspace_path):
                return False, f"Task {task.file_path} references a file being deleted ({d_path}).", [task]

    return True, "", []

_workspace_java_cache: dict = {}

def _get_workspace_java_classes(workspace_path: str) -> dict:
    global _workspace_java_cache
    key = str(Path(workspace_path).resolve())
    if key in _workspace_java_cache:
        return _workspace_java_cache[key]

    cache = {}
    for path in Path(workspace_path).rglob("*"):
        if path.is_file() and path.suffix in (".java", ".ts"):
            if "node_modules" in path.parts or "target" in path.parts or ".git" in path.parts:
                continue
            basename = path.stem
            try:
                rel_path = path.relative_to(workspace_path)
            except ValueError:
                rel_path = path
            cache.setdefault(basename, []).append(str(rel_path).replace("\\", "/"))

    _workspace_java_cache[key] = cache
    return cache

def pass_2_5_boundary_check(task, referenced_symbols: list, g_fact: str, reason: str, module_graph, workspace_path: str) -> tuple[bool, str]:
    if not module_graph:
        return True, ""

    java_classes = _get_workspace_java_classes(workspace_path)

    # 1. Start with structured LLM symbols
    symbols_to_check = set()
    for sym in referenced_symbols:
        sym_base = sym.replace(".java", "").replace(".ts", "").split(".")[-1]
        symbols_to_check.add(sym_base)

    # 2. Add prose-scan backstop from g_fact, reason, and task description
    text_to_search = (str(g_fact) + " " + str(reason) + " " + str(task.description)).replace("\\", " ")
    prose_words = re.findall(r'\b[A-Z][a-zA-Z0-9_]*\b', text_to_search)
    for word in prose_words:
        if word in java_classes:
            symbols_to_check.add(word)

    for symbol_base in symbols_to_check:
        if symbol_base in java_classes:
            candidates = java_classes[symbol_base]

            allowed_candidate_exists = False
            forbidden_candidates = []

            for target_path in candidates:
                if module_graph.can_import(task.file_path, target_path):
                    allowed_candidate_exists = True
                else:
                    target_module = module_graph.get_module_for_file(target_path)
                    forbidden_candidates.append(target_module)

            if len(candidates) > 0:
                if not allowed_candidate_exists:
                    # ALL candidates for this symbol are in forbidden modules
                    # It's an unambiguous violation
                    tmod = forbidden_candidates[0] if forbidden_candidates else "unknown"
                    return False, f"REJECTED: Pass 2.5 Architectural violation (Task references '{symbol_base}' which belongs to forbidden module '{tmod}')"
                else:
                    # Ambiguous case: some candidates are allowed, some forbidden.
                    # We only reject if the text explicitly disambiguates to a forbidden module's package path.
                    for target_path in candidates:
                        if not module_graph.can_import(task.file_path, target_path):
                            target_module = module_graph.get_module_for_file(target_path)
                            target_path_norm = target_path.replace("\\", "/")
                            if "src/main/java/" in target_path_norm:
                                pkg_path = target_path_norm.split("src/main/java/")[1].replace("/", ".")
                                pkg_path = pkg_path.replace(".java", "")
                                if pkg_path in text_to_search:
                                    return False, f"REJECTED: Pass 2.5 Architectural violation (Task explicitly references forbidden symbol '{pkg_path}' from module '{target_module}')"

                    logger.info(f"Pass 2.5: Ambiguous symbol '{symbol_base}' referenced, treated as inconclusive.")

    return True, ""

def run_gating_logic(plan, discovered_files, ranked_files, evidence_items, workspace_path, ticket_text) -> Tuple[List[Any], List[Any], bool, str, List[dict]]:
    """
    AI-IDE style gating (matches Cursor / Claude Code / Copilot Workspace behaviour):

    The planner already read the actual file contents when building the plan.
    That reading IS the grounding. We do NOT re-validate with a second LLM call.

    Rules (same as every production AI IDE):
      MODIFY / DELETE / RENAME  →  accept if file exists on disk.
                                   If not on disk, accept if discovery score ≥ 0.3.
                                   Otherwise reject (wrong file path in plan).
      CREATE                    →  always accept (new file, nothing to verify yet).
      Blocked paths             →  always reject (dist/, node_modules/, lock files, …).
      Architectural boundary    →  reject if module graph explicitly forbids the import.

    Compilation / tests are the real correctness gate — applied after generation.
    """
    rationale = getattr(plan, "rationale", "No rationale provided.")

    accepted = []
    rejected = []
    blocked_tasks = []
    task_logs = []

    # Load module graph once (used only for hard architectural boundary violations).
    try:
        from ticket_to_code.utils.dependency_graph import get_dependency_graph
        module_graph = get_dependency_graph(workspace_path)
    except Exception:
        module_graph = None

    boundary_rejected: set = set()

    # ── Pass 1: blocked paths ─────────────────────────────────────────────────
    active_tasks = []
    for task in plan.tasks:
        t_type = getattr(task.task_type, "value", str(task.task_type))
        if t_type == "read_only":
            continue
        if _is_blocked_path(task.file_path):
            blocked_tasks.append(task)
            task_logs.append({"task": task.file_path, "type": t_type, "status": "rejected", "reason": "blocked_path"})
        else:
            active_tasks.append(task)

    # ── Pass 1.5: module boundary (hard architectural violation only) ──────────
    if module_graph:
        for task in active_tasks:
            for other_task in plan.tasks:
                if task.id == other_task.id:
                    continue
                depends = other_task.id in (getattr(task, "dependencies", None) or [])
                if not depends:
                    depends = _task_references_path(task, other_task, workspace_path)
                if depends and not module_graph.can_import(task.file_path, other_task.file_path):
                    boundary_rejected.add(task.id)
                    rejected.append(task)
                    task_logs.append({
                        "task": task.file_path,
                        "type": getattr(task.task_type, "value", str(task.task_type)),
                        "status": "rejected",
                        "reason": f"Architectural boundary violation: {task.file_path} cannot depend on {other_task.file_path}",
                        "grounding_type": "none", "grounding_fact": "none",
                    })
                    break

    # ── Pass 2: file-existence gate (AI-IDE style) ────────────────────────────
    for task in active_tasks:
        if task.id in boundary_rejected:
            continue

        t_type = getattr(task.task_type, "value", str(task.task_type))

        if t_type == "create":
            # New file — nothing to verify yet; code generation + compilation is the gate.
            accepted.append(task)
            task_logs.append({"task": task.file_path, "type": t_type, "status": "accepted",
                               "reason": "create task — accepted unconditionally", "grounding_type": "create"})
            continue

        # MODIFY / DELETE / RENAME: file must exist on disk (or have strong discovery score).
        full_path = os.path.join(workspace_path, task.file_path) if workspace_path else task.file_path
        file_on_disk = os.path.exists(full_path)

        if file_on_disk:
            accepted.append(task)
            task_logs.append({"task": task.file_path, "type": t_type, "status": "accepted",
                               "reason": "file exists on disk", "grounding_type": "file_exists"})
        else:
            # Fall back to discovery score so high-confidence RAG hits still pass.
            norm = task.file_path.replace("\\", "/").lower()
            ranked = next((r for r in ranked_files if getattr(r, "file_path", "").replace("\\", "/").lower() == norm), None)
            score = float(getattr(ranked, "final_score", 0.0)) if ranked else 0.0
            if score < 0.3:
                cand = next((d for d in discovered_files if d.get("path", "").replace("\\", "/").lower() == norm), None)
                score = float(cand.get("confidence", 0.0)) if cand else 0.0

            if score >= 0.3:
                accepted.append(task)
                task_logs.append({"task": task.file_path, "type": t_type, "status": "accepted",
                                   "reason": f"file not on disk but discovery score {score:.2f} ≥ 0.3",
                                   "grounding_type": "discovery_score"})
            else:
                rejected.append(task)
                task_logs.append({"task": task.file_path, "type": t_type, "status": "rejected",
                                   "reason": f"file not found on disk and discovery score {score:.2f} < 0.3 — likely wrong path"})

    all_rejected = rejected + blocked_tasks
    is_consistent, failure_reason, _ = validate_plan_consistency(accepted, all_rejected, plan, workspace_path)
    return accepted, all_rejected, is_consistent, failure_reason, task_logs
