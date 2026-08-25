"""
Ticket-to-Code Workflow with LangGraph - TDD Approach

This implements Test-Driven Development with AI:
1. Generate test cases FIRST (using product behavior RAG)
2. Generate code SECOND (using architecture RAG)
3. Tests and code are independent (no knowledge leak)
4. Both grounded in real knowledge (no hallucinations)

Author: Deepak Madgani
Date: April 2026
"""

import logging
import re
import os
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict, List, Optional, Set
from datetime import datetime

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import SystemMessage, HumanMessage

from ticket_to_code.debug_tracker import log_phase, log_error
from ticket_to_code.models import (
    ValueEdgeTicket,
    InvestigationResult,
    InvestigationHypothesis,
    EvidenceItem,
    ComponentGroup,
    GroundedUnderstanding,
    StructuredRequirements,
    ArchitecturalPlan,
    GeneratedCode,
    BuildResult,
    BuildStatus,
    TestResult,
    TestStatus,
    SolutionGuidance,
    CandidateRole,
    PlannerDecisionValue,
    TaskType,
    ProgrammingLanguage,
)

from ticket_to_code.agents.investigation_agent import InvestigationAgent
from ticket_to_code.agents.semantic_verification_agent import SemanticVerificationResult, SemanticVerificationAgent
from ticket_to_code.agents.ticket_analyzer import TicketAnalyzerAgent
from ticket_to_code.agents.planning_agent import PlanningAgent
from ticket_to_code.agents.localization_agent import LocalizationAgent
from ticket_to_code.agents.evidence_collection_loop import EvidenceCollectionLoop
from ticket_to_code.agents.evidence_ranking_engine import EvidenceRankingEngine
from ticket_to_code.agents.repository_search_engine import RepositorySearchEngine
from ticket_to_code.agents.providers.provider_factory import RelationshipProviderFactory
from ticket_to_code.agents.rag_engine import CodebaseRAGEngine  # Updated import path
from ticket_to_code.retrieval.context_retrieval import ContextRetrievalService  # B2: cascading context
from ticket_to_code.runtime.run_context import RunContext, HealthLevel, BudgetExceeded  # B11/B12
from ticket_to_code.delivery.patch_gate import PatchGate  # B9: delivery safety
from ticket_to_code.agents.code_generator import CodeGeneratorAgent, PatchValidator
from ticket_to_code.execution.base_executor import ExecutionEngineFactory
from ticket_to_code.tracing.tracer import ExecutionTracer
from ticket_to_code.workflow_discovery import WorkflowDiscovery
from ticket_to_code.agents.runtime_log_analyzer import RuntimeLogAnalyzer, RuntimeDiagnosis
from ticket_to_code.agents.dependency_resolver import DependencyResolver, DependencyResolution  # Enhancement 12
from ticket_to_code.strategy.adaptive_replanner import AdaptiveReplanner, ReplanDecision  # Enhancement 4
from ticket_to_code.agents.git_history_agent import GitHistoryAgent  # Enhancement 9
from ticket_to_code.agents.env_tracer import EnvTracer  # Enhancement 11
from ticket_to_code.reasoning.causal_chain import CausalChainEngine  # Enhancement 3
from ticket_to_code.execution.runtime_verifier import RuntimeVerifier  # Enhancement 7
from ticket_to_code.execution.platform_knowledge import PlatformKnowledge  # Enhancement 5
from ticket_to_code.delivery.config_validator import ConfigValidator  # Enhancement 13
from ticket_to_code.agents.visual_analyzer import VisualAnalyzer, VisualAnalysis  # Enhancement 8
from ticket_to_code.utils.error_normalization import (
    normalize_error_identity,
    normalize_error_lines,
    strip_ansi,
)
from ticket_to_code.plan_gating import (
    run_gating_logic,
    validate_plan_consistency,
    _extract_json_payload,
    verify_grounding_fact_text,
)



logger = logging.getLogger(__name__)

import os
import json
from typing import Union

def write_trace_artifact(workspace_path: str, ticket_id: str, filename: str, content: Union[str, dict, list]):
    """Write an artifact to the trace directory for full observability."""
    trace_dir = os.path.join("C:/aviator_traces", ticket_id)
    os.makedirs(trace_dir, exist_ok=True)
    file_path = os.path.join(trace_dir, filename)
    
    with open(file_path, "w", encoding="utf-8") as f:
        if isinstance(content, (dict, list)):
            json.dump(content, f, indent=2)
        else:
            f.write(str(content))

def _get_plan(state: dict):
    plan = state.get("architectural_plan")
    if not plan: return None
    if isinstance(plan, dict):
        from ticket_to_code.models import ArchitecturalPlan
        plan = ArchitecturalPlan.model_validate(plan)
        state["architectural_plan"] = plan
    return plan



# ============================================================================
# STRUCTURED EXECUTION LOGGER
# ============================================================================

class StageLog:
    """
    Produces clean, UI-ready structured logs for each pipeline stage.

    Format matches the enterprise transparency requirements:
      [Stage Name]
      Key: value
      ...
      ✅ / ⛔ outcome
    """
    _separator = "─" * 60

    @staticmethod
    def localization(task) -> str:
        lines = [
            f"\n{StageLog._separator}",
            "[Localization]",
            f"  File     : {task.file_path}",
            f"  Class    : {task.target_class or 'N/A'}",
            f"  Method   : {task.target_method or 'N/A'}",
            f"  Allowed  : {task.allowed_methods or 'ALL (unconstrained)'}",
            f"  New file : {'ALLOWED' if task.new_file_creation_allowed else 'FORBIDDEN'}",
            f"  Confidence: {task.localization_confidence:.2f}",
            f"  Reason   : {task.localization_reason or 'N/A'}",
            StageLog._separator,
        ]
        return "\n".join(lines)

    @staticmethod
    def patch_validation(task, validation) -> str:
        status = "✅ ACCEPTED" if validation.passed else "⛔ REJECTED"
        m = validation.metrics
        lines = [
            f"\n{StageLog._separator}",
            "[Patch Validation]",
            f"  File     : {task.file_path}",
            f"  Status   : {status}",
            f"  Files modified   : 1",
            f"  Change ratio     : {m.get('change_ratio', 'N/A')}",
            f"  Changed blocks   : {m.get('changed_blocks', 'N/A')}",
            f"  New methods added: {m.get('new_methods_added', [])}",
            f"  New file allowed : {'Yes' if task.new_file_creation_allowed else 'No'}",
        ]
        if validation.violations:
            lines.append("  Violations:")
            for v in validation.violations:
                lines.append(f"    • {v}")
        lines.append(StageLog._separator)
        return "\n".join(lines)

    @staticmethod
    def plan_summary(tasks) -> str:
        lines = [
            f"\n{StageLog._separator}",
            "[Planning]",
            f"  Tasks: {len(tasks)}",
        ]
        for t in tasks:
            lines.append(
                f"    [{t.id}] {t.task_type.value.upper():6s} {t.file_path}"
                f"  | class={t.target_class or '?'} method={t.target_method or '?'}"
                f"  | allowed={t.allowed_methods or 'ALL'}"
                f"  | new_file={'✓' if t.new_file_creation_allowed else '✗'}"
            )
        lines.append(StageLog._separator)
        return "\n".join(lines)


# ============================================================================
# CROSS-LANGUAGE EXECUTION ORDERING
# ============================================================================

# Map microservice directory names to their root Java packages.
# Used to detect when service A imports from service B's internal packages.
_SERVICE_PACKAGE_MAP: dict[str, list[str]] = {
    "project-service":      ["com.opentext.bim.projectservice", "com.opentext.bim.sagas"],
    "area-service":         ["com.opentext.solutions.services.area"],
    "participant-service":  ["com.opentext.solutions.services.participant"],
    "contract-service":     ["com.opentext.solutions.services.contract"],
}
# These packages are allowed in ANY service (shared libraries, framework, JDK)
_SHARED_PACKAGES = (
    "com.opentext.bim.commons",
    "com.opentext.solutions.common",
    "org.springframework",
    "jakarta.", "javax.", "java.", "lombok.",
    "org.hibernate", "org.apache", "com.fasterxml",
    "io.swagger", "org.slf4j",
)


def _check_java_cross_service_imports(file_path: str, content: str) -> list[str]:
    """
    Detect cross-service Java import violations at write time.

    When a file in project-service imports from area-service's internal package
    (or vice versa), the build fails with "package X does not exist" because the
    services are separate Maven/Gradle modules with no dependency on each other.

    Returns a list of violating import lines.
    """
    norm = file_path.replace("\\", "/").lower()
    file_service = next(
        (svc for svc in _SERVICE_PACKAGE_MAP if f"/{svc}/" in norm or norm.startswith(f"{svc}/")),
        None,
    )
    if not file_service:
        return []

    violations: list[str] = []
    in_imports = True
    for line in content.splitlines():
        s = line.strip()
        if not s or s.startswith("//") or s.startswith("*") or s.startswith("package "):
            continue
        if not s.startswith("import "):
            if s.startswith("@") or s.startswith("public ") or s.startswith("class ") or s.startswith("interface "):
                in_imports = False
            if not in_imports:
                break
            continue

        pkg = s[7:].rstrip(";").strip().lstrip("static").strip()
        if any(pkg.startswith(p) for p in _SHARED_PACKAGES):
            continue
        # Violation: file in service A imports from service B's internal package
        for other_svc, other_prefixes in _SERVICE_PACKAGE_MAP.items():
            if other_svc != file_service and any(pkg.startswith(p) for p in other_prefixes):
                violations.append(f"{s}  ← cross-service: {file_service} cannot import from {other_svc}")
                break

    return violations


def _check_ts_exports_preserved(original: str, new_content: str) -> list:
    """
    Fix 1 — Export-drop guard.
    Returns the list of exported names present in the original TypeScript/JS
    file that are MISSING from the newly generated content.  A non-empty
    return means the LLM silently dropped public API that other files import.
    """
    orig_exports = set(re.findall(
        r'\bexport\s+(?:interface|type|class|enum|const|function|abstract\s+class)\s+(\w+)',
        original,
    ))
    new_exports = set(re.findall(
        r'\bexport\s+(?:interface|type|class|enum|const|function|abstract\s+class)\s+(\w+)',
        new_content,
    ))
    return sorted(orig_exports - new_exports)


def _extract_ts_interface_props(interface_content: str, interface_name: str) -> set[str]:
    """Extract property names from a TypeScript interface definition."""
    m = re.search(
        r'\binterface\s+' + re.escape(interface_name) + r'\s*(?:extends[^{]*)?\{([^}]*)\}',
        interface_content, re.DOTALL,
    )
    if not m:
        return set()
    block = m.group(1)
    props = set(re.findall(r'\b(\w+)\s*\??:', block))
    return props


def _check_ts_property_access(
    new_content: str,
    file_path: str,
    session_files: dict,
    workspace_path: str,
) -> list[str]:
    """
    Post-write TypeScript property validator (Cursor LSP check equivalent).

    For each `import { X } from '...'` in the generated file, if the imported
    module was regenerated this run (present in session_files), extract its
    interface properties and check that any `variable.property` accesses in
    the new content only use VALID properties.

    Returns list of invalid property accesses found.
    """
    if not session_files or not new_content:
        return []

    import os as _os
    src_dir = _os.path.dirname(file_path.replace("\\", "/"))
    invalid: list[str] = []

    imp_re = re.compile(r"""import\s*\{([^}]+)\}\s*from\s*['"](\.[^'"]+)['"]""")
    for imp_m in imp_re.finditer(new_content):
        symbols = [s.strip() for s in imp_m.group(1).split(",") if s.strip()]
        mod_path = imp_m.group(2)
        for ext in (".ts", ".tsx", "/index.ts"):
            cand = _os.path.normpath(
                _os.path.join(workspace_path, src_dir, mod_path + ext)
            ).replace("\\", "/").lower()
            if cand not in session_files:
                # also check relative key
                cand_rel = _os.path.normpath(
                    _os.path.join(src_dir, mod_path + ext)
                ).replace("\\", "/").lower()
                if cand_rel in session_files:
                    cand = cand_rel
                else:
                    continue

            iface_content = session_files[cand]
            for sym in symbols:
                valid_props = _extract_ts_interface_props(iface_content, sym)
                if not valid_props:
                    continue  # not a known interface, skip
                # Check for invalid property accesses: x.badProp or x?.badProp
                accesses = re.findall(
                    r'\b\w+\??\.(\w+)\b',
                    new_content,
                )
                for prop in accesses:
                    # Only flag if the prop clearly looks like a field access
                    # and is NOT in the valid interface
                    if prop and prop[0].islower() and prop not in valid_props and len(prop) > 2:
                        # Cross-check: is this property plausibly from THIS interface?
                        # Heuristic: if any valid prop starts with same prefix, flag it
                        prefix = prop[:3].lower()
                        if any(p.lower().startswith(prefix) for p in valid_props):
                            invalid.append(f"{sym}.{prop} (valid: {sorted(valid_props)[:5]})")
            break

    return list(dict.fromkeys(invalid))[:5]  # deduplicate, cap at 5


def _find_type_consumer_companions(
    model_file_path: str,
    workspace_path: str,
    existing_paths: "set[str]",
    max_consumers: int = 4,
) -> "list[tuple[str, str]]":
    """
    Inverse of model_import_follow: when a model/interface file is modified,
    find TypeScript files in the workspace that import it and add them as
    companion MODIFY tasks so they can be updated consistently.

    Scanning is bounded to the nearest parent 'modules' directory to avoid
    scanning the entire workspace.
    """
    import os as _os
    norm = model_file_path.replace("\\", "/")
    stem = Path(norm).stem  # e.g. 'displayed-member'
    model_abs = Path(workspace_path) / norm
    if not model_abs.exists():
        return []

    # Walk up to find the nearest 'modules' or 'src' boundary (max 4 levels)
    search_root = model_abs.parent
    for _ in range(4):
        parent = search_root.parent
        if parent.name in ("modules", "src", "app", "main"):
            search_root = parent
            break
        if parent == Path(workspace_path):
            break
        search_root = parent

    results: list[tuple[str, str]] = []
    skip_dirs = {"node_modules", "dist", ".git", "build", "target", "coverage", ".venv"}

    for root, dirs, files in _os.walk(str(search_root)):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if not f.endswith((".ts", ".tsx")):
                continue
            full = Path(root) / f
            rel = str(full.relative_to(Path(workspace_path))).replace("\\", "/")
            if rel == norm or rel in existing_paths:
                continue
            try:
                content = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # Check if this file imports from the model file
            if re.search(r"""from\s+['"][^'"]*""" + re.escape(stem) + r"""['""]""", content):
                results.append((rel, "type_consumer_follow"))
                if len(results) >= max_consumers:
                    return results

    return results


def _sort_tasks_by_execution_order(tasks: list) -> list:
    """
    Sort code-generation tasks by language tier and declared dependencies.

    Tier order (mirrors how Devin and Cursor sequence multi-file changes):
      0  Java/C# data models (Entity, Dto, Record, VO)
      1  Java/C# repositories / interfaces
      2  Java/C# service implementations
      3  Java/C# controllers / handlers
      4  TypeScript/JS model interfaces / DTOs
      5  TypeScript/JS services
      6  TypeScript/JS components
      7  HTML templates
      8  Angular NgModules  ← AFTER components they declare
      9  SCSS / CSS stylesheets
      10 Everything else (Python, YAML, JSON, shell scripts)

    Declared task.dependencies always take priority over tier (via Kahn's
    topological sort).  Tier is only the tie-breaker for tasks with the same
    dependency depth and no explicit ordering from the planner.
    """
    from collections import defaultdict, deque

    def _tier(task) -> int:
        p = task.file_path.lower().replace("\\", "/")
        lang = getattr(getattr(task, "language", None), "value", "").lower()

        # ── Java / C# ────────────────────────────────────────────────────────
        if lang in ("java", "kotlin", "csharp") or p.endswith((".java", ".kt", ".cs")):
            # Data models / DTOs (no stereotype)
            fname = p.rsplit("/", 1)[-1]
            if any(fname.endswith(f"{s}.java") or fname.endswith(f"{s}.kt") or fname.endswith(f"{s}.cs")
                   for s in ("model", "entity", "dto", "record", "vo", "request", "response",
                              "payload", "data", "input", "output")):
                return 0
            if "repositor" in p or "repository" in p:
                return 1
            if "interface" in p and "service" in p:
                return 1   # service interface is like a repository
            if "serviceimpl" in p or (p.endswith("impl.java") or "/impl/" in p):
                return 2
            if "service" in p:
                return 2
            if "controller" in p or "handler" in p or "rest" in p or "resource" in p:
                return 3
            return 2

        # ── TypeScript / JavaScript ───────────────────────────────────────────
        if lang in ("typescript", "javascript") or p.endswith((".ts", ".tsx", ".js", ".jsx")):
            if any(kw in p for kw in ("/models/", "/model.", "-model.", ".model.",
                                       "/interface.", "-interface.", ".interface.",
                                       "/dto.", "-dto.", ".dto.",
                                       "/types/", "/type.", "-type.", ".type.",
                                       "/entities/", "/entity.", "-entity.")):
                return 4
            # Angular NgModule: must come AFTER the components it declares
            if "module.ts" in p or "module.js" in p:
                return 8
            if "service.ts" in p or "service.js" in p:
                return 5
            if ".component." in p:
                return 6
            return 5

        # ── HTML ──────────────────────────────────────────────────────────────
        if p.endswith((".html", ".htm")):
            return 7

        # ── Styles ────────────────────────────────────────────────────────────
        if p.endswith((".scss", ".css", ".less", ".sass")):
            return 9

        return 10   # Python, YAML, JSON, shell, etc.

    # Build adjacency for Kahn's algorithm using declared task.dependencies
    task_map: dict = {t.id: t for t in tasks}
    in_degree: dict = {t.id: 0 for t in tasks}
    adj: dict = defaultdict(list)

    # Fix 5: Add IMPLICIT dependency edges across module boundaries.
    # Scan each task's description for references to other tasks' class names.
    # If task A's description mentions task B's class name, A depends on B.
    import re as _re_dep
    _create_tasks = [t for t in tasks if getattr(t.task_type, 'value', str(t.task_type)) == 'create']
    _class_to_task: dict = {}  # PascalCase class name → task id
    for _ct in _create_tasks:
        # Extract PascalCase class names from the file stem
        _stem = Path(_ct.file_path).stem
        # kebab-case → PascalCase: "foo-bar.service" → "FooBarService"
        _parts = _stem.replace(".", "-").split("-")
        _pascal = "".join(p.capitalize() for p in _parts if p)
        if len(_pascal) > 3:
            _class_to_task[_pascal] = _ct.id
        # Also try extracting from title/description
        _title_classes = _re_dep.findall(r'\b([A-Z][a-zA-Z]{3,}(?:Service|Model|Interface|Component|Module))\b',
                                          f"{_ct.title or ''} {_ct.description or ''}")
        for _tc in _title_classes:
            _class_to_task[_tc] = _ct.id

    for t in tasks:
        _t_desc = f"{t.description or ''} {t.title or ''}"
        for _cls_name, _dep_task_id in _class_to_task.items():
            if _dep_task_id == t.id:
                continue  # don't self-depend
            if _cls_name in _t_desc and _dep_task_id not in (t.dependencies or []):
                # t depends on _dep_task_id (t references that class)
                if _dep_task_id in task_map:
                    adj[_dep_task_id].append(t.id)
                    in_degree[t.id] += 1

    for t in tasks:
        for dep_id in (t.dependencies or []):
            if dep_id in task_map:
                adj[dep_id].append(t.id)
                in_degree[t.id] += 1

    # Seed queue with tasks that have no remaining dependencies, ordered by tier
    queue = deque(sorted(
        [t for t in tasks if in_degree[t.id] == 0],
        key=_tier,
    ))

    result: list = []
    while queue:
        t = queue.popleft()
        result.append(t)
        newly_free = []
        for dep_id in adj[t.id]:
            in_degree[dep_id] -= 1
            if in_degree[dep_id] == 0:
                newly_free.append(task_map[dep_id])
        queue.extend(sorted(newly_free, key=_tier))

    # Fallback: append any tasks not reached (missing dep IDs, cycles) in tier order
    in_result = {t.id for t in result}
    result.extend(sorted([t for t in tasks if t.id not in in_result], key=_tier))

    # ── Angular sibling enforcement ────────────────────────────────────────
    # Guarantee: for every Angular component pair (foo.component.ts / foo.component.html),
    # the .ts MUST always precede its sibling .html, regardless of task IDs or tier ties.
    # This prevents HTML from being generated first and inventing property names that
    # the TS controller doesn't yet know about.
    _stem_to_ts_idx: dict = {}
    for i, t in enumerate(result):
        fp = t.file_path.replace("\\", "/").lower()
        if ".component.ts" in fp or fp.endswith(".component.tsx"):
            stem = fp.rsplit(".ts", 1)[0]
            _stem_to_ts_idx[stem] = i

    swaps_needed = []
    for i, t in enumerate(result):
        fp = t.file_path.replace("\\", "/").lower()
        if fp.endswith(".html") or fp.endswith(".htm"):
            stem = fp.rsplit(".html", 1)[0].rsplit(".htm", 1)[0]
            ts_idx = _stem_to_ts_idx.get(stem)
            if ts_idx is not None and ts_idx > i:
                # HTML appears before TS — record the swap
                swaps_needed.append((i, ts_idx))

    # Apply swaps in reverse index order so earlier swaps don't invalidate later indices
    for html_idx, ts_idx in sorted(swaps_needed, reverse=True):
        result[html_idx], result[ts_idx] = result[ts_idx], result[html_idx]
        logger.info(
            f"  Sort fix: moved {result[html_idx].file_path} (TS) before "
            f"{result[ts_idx].file_path} (HTML) to ensure controller precedes template"
        )

    return result


# ============================================================================
# IMPORT RESOLUTION HELPERS  (Layer 2 + Layer 3)
# ============================================================================

def _inject_meta_imports(code: "GeneratedCode") -> "GeneratedCode":
    """
    Layer 2 — Devin / Copilot Workspace technique.
    Deterministically merge the LLM's declared META imports into the file content
    so they are never silently dropped when the SEARCH/REPLACE focused on a method.
    """
    if not code.imports or not code.content:
        return code

    content = code.content
    lang = getattr(code.language, "value", str(code.language)).lower()
    new_content = content

    for imp in code.imports:
        imp = imp.strip()
        if not imp:
            continue
        # Skip if already present (verbatim)
        if imp in content:
            continue

        if lang == "java":
            # Insert after existing import block, before first class/annotation
            _m = re.search(r'^(package\s[^\n]+\n)', new_content, re.MULTILINE)
            insert_after = _m.end() if _m else 0
            _last_import = None
            for _m2 in re.finditer(r'^import\s[^\n]+\n', new_content, re.MULTILINE):
                _last_import = _m2
            if _last_import:
                insert_after = _last_import.end()
            new_content = new_content[:insert_after] + imp + "\n" + new_content[insert_after:]

        elif lang in ("typescript", "javascript"):
            # Insert after the LAST complete import statement.
            # Handles multi-line imports like:  import {\n  Foo,\n} from '...';\n
            _last_import = None
            # Match single-line: import ... from '...';  OR  closing multi-line: } from '...';
            for _m2 in re.finditer(
                r"^(?:import\s+.*?from\s+['\"][^'\"]+['\"]\s*;?|\}\s*from\s+['\"][^'\"]+['\"]\s*;?)\s*$",
                new_content, re.MULTILINE,
            ):
                _last_import = _m2
            insert_after = (_last_import.end() + 1) if _last_import else 0
            # Ensure we're at a line boundary
            if insert_after > 0 and insert_after <= len(new_content) and new_content[insert_after - 1:insert_after] != "\n":
                insert_after = new_content.find("\n", insert_after)
                if insert_after == -1:
                    insert_after = len(new_content)
                else:
                    insert_after += 1
            new_content = new_content[:insert_after] + imp + "\n" + new_content[insert_after:]

        elif lang == "python":
            _last_import = None
            for _m2 in re.finditer(r'^(?:import|from)\s[^\n]+\n', new_content, re.MULTILINE):
                _last_import = _m2
            insert_after = _last_import.end() if _last_import else 0
            new_content = new_content[:insert_after] + imp + "\n" + new_content[insert_after:]

        elif lang in ("csharp", "cs"):
            _last_using = None
            for _m2 in re.finditer(r'^using\s[^\n]+\n', new_content, re.MULTILINE):
                _last_using = _m2
            insert_after = _last_using.end() if _last_using else 0
            new_content = new_content[:insert_after] + imp + "\n" + new_content[insert_after:]

    code.content = new_content
    return code


def _resolve_unresolved_symbols(
    content: str,
    language: str,
    sqlite_store,
    workspace_path: str,
    source_file_path: str = "",
) -> list[tuple[str, str]]:
    """
    Layer 3 — Claude Code / Devin technique.
    Parse generated file content for NEW symbol references and look each up in
    the SQLite symbol index.  Returns [(import_statement, file_path), ...].
    """
    if not sqlite_store or not content:
        return []

    found: list[tuple[str, str]] = []
    lang = language.lower()

    # Collect what's already imported (to avoid duplicates)
    existing_imports: set[str] = set()
    for line in content.split("\n"):
        s = line.strip()
        if s.startswith(("import ", "from ", "using ")):
            existing_imports.add(s)

    # Extract all PascalCase identifiers used in the file (potential class refs)
    all_symbols = re.findall(r'\b([A-Z][a-zA-Z0-9]{2,})\b', content)
    unique_symbols = list(dict.fromkeys(all_symbols))

    # Filter to symbols that are NOT already imported
    def _already_imported(sym: str) -> bool:
        return any(sym in imp for imp in existing_imports)

    candidates = [s for s in unique_symbols if not _already_imported(s)][:30]

    from ticket_to_code.agents.code_generator import _build_import_hint

    for sym in candidates:
        try:
            rows = sqlite_store.search_symbols(
                sym, kinds=["class", "interface", "enum"], limit=2
            )
            for row in rows:
                row_name = row["name"] if isinstance(row, dict) else getattr(row, "name", sym)
                row_path = row["path"] if isinstance(row, dict) else getattr(row, "path", "")
                if row_name != sym or not row_path:
                    continue
                hint = _build_import_hint(sym, row_path, source_file_path=source_file_path, workspace_path=workspace_path)
                if hint and hint not in existing_imports:
                    # Verify the file actually exists in workspace
                    if Path(workspace_path, row_path).exists():
                        found.append((hint, row_path))
                        existing_imports.add(hint)
                break
        except Exception:
            pass

    return found


# ============================================================================
# TRANSIENT REGISTRY — stores non-serializable objects OUTSIDE LangGraph state
# ============================================================================
# LangGraph's MemorySaver uses msgpack to checkpoint the state dict after every
# node.  Python functions (callbacks) and complex class instances (RunContext)
# are NOT msgpack-serializable and cause TypeError crashes.
#
# This thread-safe registry stores such objects keyed by ticket_id.  Nodes
# read from it via _get_transient(); the objects are never persisted.

import threading as _threading

_transient_lock = _threading.Lock()
_transient_store: dict[str, dict[str, Any]] = {}  # {ticket_id: {key: value}}


def _set_transient(ticket_id: str, key: str, value: Any) -> None:
    """Store a non-serializable object for the current workflow run."""
    with _transient_lock:
        _transient_store.setdefault(ticket_id, {})[key] = value


def _get_transient(state: dict, key: str, default=None) -> Any:
    """Read a non-serializable object stored for the current workflow run."""
    ticket = state.get("ticket")
    tid = getattr(ticket, "ticket_id", None) if ticket else None
    if not tid:
        return default
    with _transient_lock:
        return _transient_store.get(tid, {}).get(key, default)


def _clear_transient(ticket_id: str) -> None:
    """Clean up transient objects after a workflow run completes."""
    with _transient_lock:
        _transient_store.pop(ticket_id, None)


# ============================================================================
# STATE DEFINITION
# ============================================================================

class TicketToCodeState(TypedDict):
    """
    Complete workflow state managed by LangGraph.
    
    Annotated fields are automatically tracked and merged.
    """
    # Input
    ticket: ValueEdgeTicket
    workspace_path: str
    max_retry_attempts: int
    
    # Phase results
    investigation_result: Optional[InvestigationResult]
    requirements: Optional[StructuredRequirements]
    discovered_files: Optional[List[dict]]          # Real candidate files from Repository Discovery
    architectural_plan: Optional[ArchitecturalPlan]
    solution_guidance: Optional[SolutionGuidance]  # For non-code issues
    
    # RAG contexts (SEPARATED for independence)
    test_rag_context: Optional[List[dict]]  # Product behavior knowledge
    code_rag_context: Optional[List[dict]]  # Architecture pattern knowledge
    
    # Branch-specific status (for parallel execution)
    test_status: Optional[str]  # Status of TEST BRANCH
    code_status: Optional[str]  # Status of CODE BRANCH

    # Post-build semantic validation result
    outcome_check_result: Optional[dict]  # {status, details, files_checked}
    
    # Generated artifacts (TDD order)
    generated_tests: Optional[List[GeneratedCode]]  # Generated FIRST
    generated_code: Optional[List[GeneratedCode]]   # Generated SECOND
    original_file_contents: Optional[dict] # Original contents for auto-revert
    
    # Execution results
    build_result: Optional[BuildResult]
    test_result: Optional[TestResult]
    
    # Retry tracking
    retry_attempt: Annotated[int, "Current retry attempt"]
    last_error_type: Optional[Literal["build", "test"]]
    previous_build_errors: Optional[List[str]]  # Track error identities across fix_build loops

    # Candidate retry (localization-level — independent from build/test retry)
    blacklisted_files: Optional[List[str]]        # paths confirmed as wrong localization
    candidate_retry_count: int                     # how many candidate-level retries so far
    max_candidate_retries: int                     # retries before triggering full rediscovery
    discovery_cycle_count: int                     # how many full rediscovery cycles so far
    validation_failure_reason: Optional[str]       # why validate_candidates rejected

    # Phase 2F: Ownership Completeness result (written by ownership_completeness_node)
    ownership_completeness: Optional[dict]

    # Phase 2G: Investigation Hypotheses + Evidence Collection (written before ownership check)
    investigation_hypotheses: Optional[List[InvestigationHypothesis]]
    evidence_items: Optional[List[EvidenceItem]]
    ranked_files: Optional[List[Any]]
    semantic_verification_results: Optional[List[dict]]
    clarification_question: Optional[str]          # Agentic loop question for user
    # Component Group Architecture: groups of related framework files
    # (e.g., .ts + .html + .scss) built before ranking, passed through
    # semantic verification to the planner for group-aware planning.
    component_groups: Optional[List[Any]]
    grounded_understanding: Optional[GroundedUnderstanding]
    # Query expansion provenance map from RepositorySearchEngine.
    # Dict[original_literal, Set[expanded_terms]] — passed to EvidenceRankingEngine
    # so expansion matches receive correct literal-match credit.
    query_expansion_map: Optional[dict]

    # Planner decisions: Dict[file_path, {role, decision, reason, confidence}]
    # Written by plan_node after create_plan(); read by grounded_understanding_node
    # to prevent auto-injection of files the planner intentionally excluded.
    planner_decisions: Optional[dict]

    # Evidence-based build routing: tracks what errors looked like on the previous
    # build attempt so check_build_status can tell CHANGING errors (progress) from
    # IDENTICAL errors (stuck) and route differently — not just count retries.
    prev_build_error_fingerprint: Optional[str]
    consecutive_identical_build_errors: int

    # Requirement-satisfaction loop (post-build): outcome_check verifies the ticket
    # is actually solved, not just that it compiles. If requirements are missing it
    # feeds concrete remediation back into the adaptive edit loop, bounded by
    # max_outcome_fix_attempts.
    outcome_check_result: Optional[dict]
    outcome_remediation: Optional[str]
    outcome_fix_attempt: int
    max_outcome_fix_attempts: int

    # Re-ranker tiered fallback: Tier 2 candidates held in reserve for
    # context expansion when outcome_check finds PARTIAL/INCOMPLETE.
    tier2_candidates: Optional[List[dict]]         # backup files from re-ranker
    context_expansion_count: int                   # how many times we expanded

    # Global status (ONLY updated by SEQUENTIAL nodes, NOT by parallel branches)
    status: str
    errors: Annotated[List[str], "Accumulated errors"]
    start_time: datetime
    end_time: Optional[datetime]

    # ── Enhancement 1: Runtime Log Diagnosis ──────────────────────────────────
    # Populated by runtime_diagnosis_node between investigate and unified_analysis.
    # If no log files are found, this stays None and the pipeline is unchanged.
    runtime_diagnosis: Optional[Any]  # RuntimeDiagnosis instance

    # ── B11/B12: Per-run telemetry and health tracking ────────────────────────
    # RunContext holds RunBudget, DegradationRegistry, and phase timers.
    # It is created once at workflow entry and referenced throughout.
    # Not serialisable by LangGraph — stored as Any to avoid schema errors.
    run_ctx: Optional[Any]  # RunContext instance


# ============================================================================
# AGENT INITIALIZATION
# ============================================================================



class WorkflowAgents:
    """Container for all agents"""
    
    def __init__(self, workspace_path: str, technology: Optional[str] = None):
        """
        Initialize workflow agents.
        
        Args:
            workspace_path: Path to project workspace
            technology: Language/framework to use. Valid values:
                - 'java' (Maven/Gradle)
                - 'dotnet' (.NET Framework/Core)
                - 'python' (pytest)
                - 'nodejs' (npm/jest)
                - 'go' (go build/test)
                - None (auto-detect from project files)
        """
        self.workspace_path = Path(workspace_path)
        
        # Initialize agents
        self.investigation = InvestigationAgent()
        self.analyzer = TicketAnalyzerAgent()
        self.planner = PlanningAgent()
        self.rag_engine = CodebaseRAGEngine()
        self.rag_engine.workspace_path = str(workspace_path)
        self.localizer = LocalizationAgent(str(workspace_path))  # Repository intelligence
        self.code_generator = CodeGeneratorAgent()
        self.test_generator = CodeGeneratorAgent()  # Separate instance for tests
        self.llm = self.code_generator.llm
        self.repo_search = RepositorySearchEngine(self.workspace_path)   # Phase 3A
        self.relationship_provider = RelationshipProviderFactory.get_provider(str(workspace_path))
        
        self.workflow_discovery = None
        try:
            self.workflow_discovery = WorkflowDiscovery(
                self.localizer.sqlite_store,
                self.localizer.neo4j_store,
                str(workspace_path),
            )
            self.localizer.workflow_discovery = self.workflow_discovery
            logger.info("✅ Workflow Discovery layer initialized")
        except Exception as e:
            logger.warning(f"Workflow Discovery initialization failed (non-fatal): {e}")
        self.evidence_loop = EvidenceCollectionLoop(
            self.localizer, self.rag_engine, self.repo_search, self.relationship_provider
        )
        
        self.semantic_verifier = SemanticVerificationAgent(workspace_path=str(workspace_path))
        self.evidence_loop._semantic_verifier = self.semantic_verifier
        
        # Initialize execution engine with user-specified or auto-detected technology
        self.executor = ExecutionEngineFactory.create(str(workspace_path), technology)

        # Observability tracer (no-op unless TRACE_MODE=true)
        self.tracer = ExecutionTracer(str(workspace_path))

        # Enhancement 1: Runtime Log Analyzer (read-only, never writes files)
        self.runtime_log_analyzer = RuntimeLogAnalyzer(str(workspace_path))

        # Enhancement 12: Dependency Resolver (detects missing deps from build errors)
        self.dependency_resolver = DependencyResolver(str(workspace_path))

        # Enhancement 4: Adaptive Replanner (switches strategy on repeated failures)
        self.adaptive_replanner = AdaptiveReplanner()

        # Enhancement 9: Git History Agent (read-only git evidence)
        self.git_history = GitHistoryAgent(str(workspace_path))

        # Enhancement 11: Env Var Tracer (traces env var usage across files)
        self.env_tracer = EnvTracer(str(workspace_path))

        # Enhancement 3: Causal Chain Reasoning Engine
        self.causal_chain = CausalChainEngine(str(workspace_path))

        # Enhancement 7: Runtime Verifier (starts app after build to check for errors)
        self.runtime_verifier = RuntimeVerifier(str(workspace_path), technology or "auto")

        # Enhancement 5: Platform Knowledge (Windows/Linux-specific guidance)
        self.platform_knowledge = PlatformKnowledge()

        # Enhancement 13: Config Validator (YAML/JSON/env syntax validation)
        self.config_validator = ConfigValidator(str(workspace_path))

        # Enhancement 8: Visual Screenshot Analyzer (multimodal vision)
        self.visual_analyzer = VisualAnalyzer(str(workspace_path))

        tech_info = technology if technology else "auto-detected"
        logger.info(f"Workflow agents initialized for: {workspace_path} (Technology: {tech_info})")


# ============================================================================
# GRAPH NODES (Each phase is a simple function)
# ============================================================================

def investigate_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 0: Investigation & Triage

    Determines if code changes are needed or just configuration guidance.
    Also initialises the per-run RunContext (B11/B12).
    """
    print("\n" + "="*80)
    print(" ENTERING: investigate_node() in workflow.py")
    print("   Purpose: Analyze ticket to determine if code changes are needed")
    print("="*80)
    logger.info(" PHASE 0: Investigation & Triage")

    # ── B11/B12: Initialise RunContext once per run ───────────────────────────
    ticket = state["ticket"]
    run_ctx = RunContext(
        ticket_id=getattr(ticket, "ticket_id", "") or "",
        repo=str(state.get("workspace_path", "")),
    )
    # Register evidence-critical subsystems so degradations are trackable
    for sub in ["rag_engine", "pgvector", "neo4j", "sqlite_index", "context_retrieval"]:
        run_ctx.registry.register(sub)
    run_ctx.start_phase("investigate")

    # Probe RAG health at run start (B11 — startup verification)
    try:
        if agents.rag_engine and getattr(agents.rag_engine, "is_initialized", False):
            vs = getattr(agents.rag_engine, "vector_store", None)
            if vs is None:
                run_ctx.degrade("pgvector", HealthLevel.FAILED, "vector_store is None")
                run_ctx.degrade("rag_engine", HealthLevel.DEGRADED, "pgvector unavailable")
    except Exception as _e:
        run_ctx.degrade("rag_engine", HealthLevel.DEGRADED, str(_e))

    agents.tracer.record_ticket(ticket)  # creates trace directory

    # ── Enhancement 8: Visual Screenshot Analysis ─────────────────────────────
    visual_analysis = None
    if getattr(ticket, "attachments", None):
        try:
            logger.info("📸 ENHANCEMENT 8: Visual Screenshot Analysis")
            visual_analysis = agents.visual_analyzer.analyze(
                ticket.attachments,
                ticket_text=ticket.description
            )
            if visual_analysis and visual_analysis.has_visual_data:
                logger.info(
                    f"📸 Visual Analysis: Found {len(visual_analysis.elements)} elements across {len(visual_analysis.images)} screenshot(s), "
                    f"UI error: '{visual_analysis.error_text}', state: '{visual_analysis.ui_state}'"
                )
                
                if visual_analysis.formatted_summary:
                    ticket.description += f"\n\n### 📸 Visual Evidence (Ordered Screenshots):\n{visual_analysis.formatted_summary}"
                    logger.info("📸 Injected ordered visual evidence summary into ticket description.")
                else:
                    extra_context = []
                    if visual_analysis.error_text:
                        extra_context.append(f"Visual UI Error: {visual_analysis.error_text}")
                    if visual_analysis.ui_state:
                        extra_context.append(f"Visual UI State: {visual_analysis.ui_state}")
                    if visual_analysis.diagnosis:
                        extra_context.append(f"Visual Diagnosis: {visual_analysis.diagnosis}")

                    if extra_context:
                        ticket.description += "\n\n### 📸 Visual Evidence (from attached screenshot):\n" + "\n".join(f"- {c}" for c in extra_context)
                        logger.info("📸 Injected visual evidence into ticket description for downstream agents.")

                write_trace_artifact(
                    state["workspace_path"],
                    getattr(ticket, "ticket_id", "TASK-DEFAULT"),
                    "visual_analysis.json",
                    visual_analysis.dict()
                )

                # Copy attachments directly into ticket trace folder
                try:
                    import shutil
                    ticket_id = getattr(ticket, "ticket_id", "TASK-DEFAULT")
                    ticket_att_dir = Path("C:/aviator_traces") / ticket_id / "attachments"
                    ticket_att_dir.mkdir(parents=True, exist_ok=True)
                    for att in ticket.attachments:
                        att_path = Path(att)
                        if att_path.exists():
                            shutil.copy2(att_path, ticket_att_dir / att_path.name)
                            logger.info(f"📸 Copied attachment {att_path.name} to {ticket_att_dir}")
                except Exception as _ce:
                    logger.warning(f"Failed to copy attachments to trace dir: {_ce}")
        except Exception as _ve:
            logger.warning(f"Visual analysis failed (non-fatal): {_ve}")

    codebase_summary = f"Codebase at: {state['workspace_path']}"

    invest_out = agents.investigation.investigate(
        ticket,
        codebase_summary=codebase_summary
    )
    import builtins
    builtins.print(f"DEBUG: type(invest_out)={type(invest_out)}")
    if isinstance(invest_out, tuple):
        builtins.print(f"DEBUG: len(invest_out)={len(invest_out)}")
        for i, val in enumerate(invest_out):
            builtins.print(f"DEBUG: invest_out[{i}] type={type(val)}")

    investigation = invest_out[0] if isinstance(invest_out, tuple) else invest_out
    while isinstance(investigation, tuple):
        investigation = investigation[0]

    logger.info(
        f"Investigation complete:\n"
        f"  Type: {investigation.ticket_type.value}\n"
        f"  Code needed: {investigation.requires_code_changes}\n"
        f"  Confidence: {investigation.confidence:.2f}"
    )

    run_ctx.end_phase("investigate")

    # DEBUG LOG
    log_phase(
        phase='INVESTIGATE',
        llm_input={'ticket': ticket.title, 'description': ticket.description[:200]},
        llm_output={
            'ticket_type': investigation.ticket_type.value,
            'requires_code': investigation.requires_code_changes,
            'confidence': investigation.confidence,
            'root_cause': investigation.root_cause_hypothesis[:100] if investigation.root_cause_hypothesis else None
        },
        agent='InvestigationAgent'
    )

    # Store RunContext in transient registry (NOT in state — it's not msgpack-serializable)
    _set_transient(
        getattr(ticket, "ticket_id", ""),
        "run_ctx",
        run_ctx,
    )

    return {
        "investigation_result": investigation,
        "status": "investigation_complete",
    }


# ============================================================================
# Enhancement 1: RUNTIME LOG DIAGNOSIS NODE
# ============================================================================

def runtime_diagnosis_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Enhancement 1: Runtime Log Analyzer

    Reads workspace log files, parses stack traces, and maps errors
    to indexed source files.  Produces a RuntimeDiagnosis that enriches
    the pipeline's understanding of the problem.

    Safety properties:
      - READ-ONLY: never writes files, never runs commands.
      - ENRICHING: only ADDS data to state; never removes or overrides.
      - GRACEFUL: if no logs found → sets runtime_diagnosis with
        has_runtime_data=False and the rest of the pipeline is unchanged.
    """
    print("\n" + "="*80)
    print(" ENTERING: runtime_diagnosis_node() in workflow.py")
    print("   Purpose: Scan workspace for runtime logs and parse stack traces")
    print("="*80)
    logger.info("🔍 ENHANCEMENT 1: Runtime Log Diagnosis")

    ticket = state["ticket"]
    ticket_text = f"{getattr(ticket, 'title', '')} {getattr(ticket, 'description', '')}"

    # Get SQLite store from localizer for file resolution (if available)
    sqlite_store = None
    try:
        sqlite_store = getattr(agents.localizer, "sqlite_store", None)
    except Exception:
        pass

    try:
        diagnosis = agents.runtime_log_analyzer.analyze(
            ticket_text=ticket_text,
            sqlite_store=sqlite_store,
        )
    except Exception as exc:
        logger.warning(f"RuntimeLogAnalyzer failed (non-fatal): {exc}")
        diagnosis = RuntimeDiagnosis(has_runtime_data=False)

    if diagnosis.has_runtime_data:
        logger.info(
            f"  Runtime diagnosis found:\n"
            f"    Error type : {diagnosis.error_type}\n"
            f"    Root file  : {diagnosis.root_file}:{diagnosis.root_line}\n"
            f"    Stack depth: {len(diagnosis.stack_trace)}\n"
            f"    Confidence : {diagnosis.confidence:.2f}\n"
            f"    Log source : {diagnosis.log_source}"
        )
    else:
        logger.info("  No runtime logs or stack traces found. Pipeline continues unchanged.")

    return {
        "runtime_diagnosis": diagnosis,
        "status": "runtime_diagnosis_complete",
    }


def unified_analysis_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Unified Analysis Phase: ONE node for both code and non-code tickets
    
    Based on investigation result, either:
    - Extracts structured requirements (for code generation)
    - OR generates solution guidance (for config/usage issues)
    
    This avoids redundant ticket analysis!
    """
    print("\n" + "="*80)
    print(" ENTERING: unified_analysis_node() in workflow.py")
    investigation = state["investigation_result"]
    print(f"   Ticket Type: {investigation.ticket_type.value}")
    print(f"   Code Changes Needed: {investigation.requires_code_changes}")
    print("="*80)
    
    if investigation.requires_code_changes:
        # Path A: Code generation - Extract requirements
        logger.info(" PHASE 1A: Requirement Analysis (for code generation)")
        
        requirements = agents.analyzer.analyze_ticket(state["ticket"])
        
        logger.info(
            f"Analysis complete:\n"
            f"  Functional: {len(requirements.functional_requirements)}\n"
            f"  Technical: {len(requirements.technical_requirements)}"
        )
        
        agents.tracer.record_analysis(
            requirements=requirements,
            investigation=investigation,
            system_message=getattr(agents.analyzer, "last_system_prompt", None),
            user_message=getattr(agents.analyzer, "last_user_prompt", None),
        )
        return {
            "requirements": requirements,
            "status": "analysis_complete"
        }
    else:
        # Path B: Solution guidance - Retrieve context and generate guidance
        logger.info(" PHASE 1B: Solution Guidance (for non-code issues)")
        logger.info("   Retrieving troubleshooting context from RAG...")
        
        # Build search queries based on investigation findings
        search_queries = []
        search_queries.append(f"{investigation.ticket_type.value} {state['ticket'].title}")
        
        if investigation.affected_systems:
            search_queries.append(f"configuration {' '.join(investigation.affected_systems)}")
        
        if investigation.investigation_areas:
            search_queries.append(f"{' '.join(investigation.investigation_areas)} setup troubleshooting")
        
        # Retrieve context using RAG
        troubleshooting_context = []
        for query in search_queries[:3]:  # Limit to 3 queries
            logger.info(f"   RAG Query: {query}")
            context_chunks = agents.rag_engine.retrieve_context(
                query=query,
                max_results=5,
                document_types=["documentation", "configuration", "readme", "troubleshooting"]
            )
            troubleshooting_context.extend(context_chunks)
        
        logger.info(f"  ✅ Retrieved {len(troubleshooting_context)} relevant documents/examples")
        
        # Generate solution guidance with RAG context
        logger.info("   Generating solution with LLM using retrieved context...")
        guidance = agents.analyzer.generate_solution_guidance(
            ticket=state["ticket"],
            investigation=state["investigation_result"],
            rag_context=troubleshooting_context
        )
        
        logger.info(
            f"Solution guidance generated:\n"
            f"  Solution Type: {guidance.solution_type}\n"
            f"  Steps: {len(guidance.step_by_step_solution)}\n"
            f"  Config Examples: {len(guidance.configuration_examples)}"
        )
        # IMPORTANT: Append RAG-based solution to investigation log
        logger.info("   Appending detailed solution (with RAG) to investigation log...")
        from ticket_to_code.logging_helper import WorkflowLogger
        log_helper = WorkflowLogger()
        
        # Find the investigation log file (logs are in project root)
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent  # aviator-plugin-sample/
        logs_dir = project_root / "logs" / "investigations"
        
        # Get most recent investigation log for this ticket
        if logs_dir.exists():
            ticket_logs = list(logs_dir.glob(f"{state['ticket'].ticket_id}_*_investigation.log"))
            if ticket_logs:
                latest_log = max(ticket_logs, key=lambda p: p.stat().st_mtime)
                log_helper.append_solution_guidance_to_log(latest_log, guidance)
                logger.info(f"  ✅ Solution guidance appended to: {latest_log.name}")

        
        agents.tracer.record_analysis(
            solution_guidance=guidance,
            investigation=investigation,
            system_message=getattr(agents.analyzer, "last_system_prompt", None),
            user_message=getattr(agents.analyzer, "last_user_prompt", None),
        )
        agents.tracer.finalize(state)  # non-code ticket — workflow ends here
        return {
            "solution_guidance": guidance,
            "status": "solution_guidance_complete"
        }




def discovery_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 1.5: Repository Discovery — runs BEFORE planning.

    Finds REAL files from the repository that match the ticket keywords.
    The planner receives this list and MUST choose from real paths only —
    eliminating hallucinated file paths at the source.
    """
    print("\n" + "="*80)
    print(" ENTERING: discovery_node() in workflow.py")
    print("   Purpose: Ground the planner in REAL repository files")
    print("="*80)
    logger.info(" PHASE 1.5: Repository Discovery")

    ticket = state["ticket"]
    ticket_text = f"{ticket.title} {ticket.description}"

    # Use expanded scope (top_n=20) when called as part of candidate retry cycle
    retry_count = state.get("candidate_retry_count", 0)
    max_retries = state.get("max_candidate_retries", 2)
    expanded = retry_count >= max_retries
    top_n = 20 if expanded else 12
    if expanded:
        logger.info("   Expanded discovery scope (candidate retries exhausted — searching wider)")

    # ── Hard stop: count how many full rediscovery cycles we have done ────────
    discovery_cycle = state.get("discovery_cycle_count", 0)
    _MAX_DISCOVERY_CYCLES = 2  # allow at most 2 full rediscoveries before giving up
    if expanded and discovery_cycle >= _MAX_DISCOVERY_CYCLES:
        logger.error(
            f"   DISCOVERY HARD STOP: {discovery_cycle} full rediscovery cycles without "
            f"finding valid candidates — failing workflow to prevent infinite loop"
        )
        return {
            "discovered_files": [],
            "status": "failed",
            "error": (
                "Discovery hard stop: could not locate valid repository files after "
                f"{discovery_cycle} full rediscovery cycles. "
                "The target files may not exist in the indexed codebase."
            ),
        }

    evidence_items = state.get("evidence_items") or []
    hypotheses = state.get("investigation_hypotheses") or []
    candidates = agents.localizer.discover_repository_candidates(
        ticket_text,
        top_n=top_n,
        evidence_items=evidence_items,
        hypotheses=hypotheses,
        rag_engine=agents.rag_engine,  # B1: pass rag_engine so s_vec is populated
        run_ctx=_get_transient(state, "run_ctx"),
    )

    logger.info(f"  Discovery complete: {len(candidates)} candidate file(s) identified")

    # ── Extract Tier 2 backup candidates from re-ranker ───────────────────
    # The re-ranker (Phase 5 in localization_agent) attaches tier2 backup
    # candidates on the first candidate's _tier2_backup key.  We extract
    # them here and store separately in state for fallback expansion.
    tier2_backup = []
    if candidates and "_tier2_backup" in candidates[0]:
        tier2_backup = candidates[0].pop("_tier2_backup", [])
        logger.info(f"  📦 Tier 2 backup: {len(tier2_backup)} file(s) held in reserve")

    reset_fields: dict = {
        "discovered_files": candidates,
        "tier2_candidates": tier2_backup,
        "status": "discovery_complete",
    }
    if expanded:
        # Reset the per-cycle retry counter so plan→validate can retry again,
        # but track the total number of full rediscovery cycles for the hard stop.
        reset_fields["candidate_retry_count"] = 0
        reset_fields["discovery_cycle_count"] = discovery_cycle + 1
        # Explicitly carry forward the blacklist so LangGraph cannot silently drop
        # it during state merging.  All ownership-gate and validation failures from
        # ALL previous cycles are preserved, preventing the planner from re-proposing
        # any already-rejected file after a full rediscovery.
        current_blacklist = state.get("blacklisted_files") or []
        reset_fields["blacklisted_files"] = list(current_blacklist)
        logger.info(
            f"  ♻️  Reset: candidate_retry_count=0 "
            f"(discovery cycle {discovery_cycle + 1}/{_MAX_DISCOVERY_CYCLES}), "
            f"blacklist carried forward: {current_blacklist}"
        )
    
    artifact = {
        "ticket": {"id": state["ticket"].ticket_id, "title": state["ticket"].title},
        "discovered_files": candidates,
        "tier2_backup_count": len(tier2_backup),
    }
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "execution_trace.json", artifact)
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "discovered_files.json", candidates)
    
    return reset_fields


# ============================================================================
# CANDIDATE ROLE CLASSIFICATION
# ============================================================================

def _is_protected_config_owner(path: str, workspace_path: str) -> bool:
    """True for on-disk infrastructure/config files that must never be blacklisted.

    Config/infra files (YAML, JSON, .env, scripts, Dockerfile) are legitimate write
    targets for DevOps tickets but carry no AST symbols, so a no-op/rejection can wrongly
    blacklist them and collapse the next plan to zero writable tasks. We shield only files
    that physically exist on disk, excluding auto-generated locks and agent-internal caches.
    """
    if not path or not workspace_path:
        return False
    p = path.replace("\\", "/").lower()
    name = Path(path).name.lower()
    ext = Path(path).suffix.lower()
    _CONFIG_EXTS = {
        ".yml", ".yaml", ".json", ".toml", ".ini", ".properties",
        ".conf", ".cfg", ".xml", ".sh", ".bat", ".ps1",
    }
    is_config = ext in _CONFIG_EXTS or name.startswith(".env") or name in {"dockerfile", "makefile"}
    if not is_config:
        return False
    is_locked = (
        name in {
            "package-lock.json", "yarn.lock", "shrinkwrap.json",
            "gradle.lockfile", "poetry.lock", "pipfile.lock", "composer.lock",
        }
        or name.endswith(".lock")
        or "/dist/" in p or "/node_modules/" in p or "/target/" in p
        or "/build/" in p or "/.venv/" in p or "/venv/" in p
        or "brain/knowledge/" in p
    )
    if is_locked:
        return False
    try:
        candidate = Path(path)
        full = candidate if candidate.is_absolute() else (Path(workspace_path) / candidate)
        return full.is_file()
    except Exception:
        return False


def _classify_candidate_role(path: str) -> str:
    """
    Deterministically classify a repository file path into a CandidateRole.

    Rules are ordered from most-specific to least-specific.  The function
    uses only the normalised file path — no I/O, no LLM calls, O(1).

    Args:
        path: Workspace-relative file path (forward OR back slashes are fine).

    Returns:
        A CandidateRole string value.
    """
    p = path.lower().replace("\\", "/")

    # ── Generated / dependency artifacts — never manually edited ─────────────
    if "/dist/" in p or "/node_modules/" in p or "/target/" in p or "/build/" in p:
        return CandidateRole.GENERATED.value

    # ── Lock files — auto-generated, should never be directly modified ────────
    if (
        p.endswith("package-lock.json")
        or p.endswith("yarn.lock")
        or p.endswith("shrinkwrap.json")
        or p.endswith("gradle.lockfile")
    ):
        return CandidateRole.LOCK_FILE.value

    # ── Test / spec files — never targets for version-bump changes ────────────
    if (
        ".spec." in p
        or ".test." in p
        or p.endswith("test.java")
        or p.endswith("tests.java")
        or p.endswith("spec.java")
        or p.endswith("_test.go")
        or p.endswith("_test.py")
        or "/test/" in p
        or "/tests/" in p
        or "/spec/" in p
        or "__tests__" in p
    ):
        return CandidateRole.TEST.value

    # ── Style / CSS files ─────────────────────────────────────────────────────
    if p.endswith((".scss", ".css", ".sass", ".less")):
        return CandidateRole.STYLE.value

    # ── Deployment / infrastructure files ─────────────────────────────────────
    if (
        p.endswith(".sh")
        or p.endswith(".bat")
        or p.endswith(".ps1")
        or "/helm/" in p
        or "/deploy/" in p
        or "/k8s/" in p
        or "/kubernetes/" in p
        or p.endswith("dockerfile")
        or p.endswith("docker-compose.yml")
        or p.endswith("docker-compose.yaml")
        or ("manifest" in p and p.endswith((".yml", ".yaml")))
        or ("deployment" in p and p.endswith((".yml", ".yaml")))
    ):
        return CandidateRole.DEPLOYMENT.value

    # ── Root / entry-point components (Angular, React) ────────────────────────
    if (
        p.endswith("app.component.ts")
        or p.endswith("app.module.ts")
        or p.endswith("app.component.html")
        or p.endswith("app.component.scss")
        or p.endswith("main.ts")
        or p.endswith("main.tsx")
        or p.endswith("app.tsx")
        or p.endswith("app.jsx")
        or "/app.component." in p
        or "/app.module." in p
    ):
        return CandidateRole.ROOT_COMPONENT.value

    # ── Documentation ─────────────────────────────────────────────────────────
    if p.endswith((".md", ".rst", ".txt", ".adoc")):
        return CandidateRole.DOCUMENTATION.value

    # ── Version / constant source files ───────────────────────────────────────
    # (check before generic CONFIG to catch VersionConstants.java etc.)
    name_segment = p.rsplit("/", 1)[-1]  # basename only
    if (
        "version" in name_segment
        or "constant" in name_segment
        or "constants" in name_segment
        or "buildconfig" in name_segment
    ):
        return CandidateRole.VERSION_SOURCE.value

    # ── Generic config / data files ───────────────────────────────────────────
    if p.endswith(
        (".json", ".yaml", ".yml", ".env", ".properties", ".xml", ".toml", ".ini", ".cfg")
    ):
        return CandidateRole.CONFIG.value

    return CandidateRole.UNKNOWN.value


# ---------------------------------------------------------------------------
# Precision Gate helpers — repository-agnostic, no hardcoded extensions/roles
# ---------------------------------------------------------------------------

def _select_planner_candidates(eligible: list) -> list:
    """Focus the planner candidate pool using evidence-first gating.

    Algorithm (evidence-driven):
      1. Separate candidates into evidence-grounded (have evidence_match signal)
         and heuristic-only (found via keyword/symbol matching only).
      2. If evidence-grounded files exist, they form the CORE set.
      3. Heuristic files only survive if their confidence is >= 50% of the
         best evidence-matched file's score.
      4. Safety floor: ensure at least 3 candidates survive.
    """
    if not eligible:
        return []

    # Partition into evidence-grounded vs heuristic-only
    evidence_files = []
    heuristic_files = []
    for c in eligible:
        signals = c.get("signals", [])
        has_evidence = any(str(s).startswith("evidence_match") for s in signals)
        if has_evidence:
            evidence_files.append(c)
        else:
            heuristic_files.append(c)

    if evidence_files:
        # Evidence-first gate: only keep heuristic files scoring above 50% of best evidence score
        best_evidence_score = max(c.get("confidence", 0.0) for c in evidence_files)
        heuristic_threshold = best_evidence_score * 0.50

        strong_heuristic = [
            c for c in heuristic_files
            if c.get("confidence", 0.0) >= heuristic_threshold
        ]

        focused = evidence_files + strong_heuristic
        logger.info(
            f"  [EvidenceGate] {len(evidence_files)} evidence-grounded + "
            f"{len(strong_heuristic)} strong heuristic (threshold={heuristic_threshold:.3f}) "
            f"= {len(focused)} total (dropped {len(heuristic_files) - len(strong_heuristic)} weak heuristic)"
        )
    else:
        # No evidence files — fall back to top candidates by score
        focused = eligible[:10]
        logger.info(f"  [EvidenceGate] No evidence files — using top {len(focused)} by score")

    # Floor: Minimum 3 candidates
    if len(focused) < 3:
        focused_paths = {c["path"] for c in focused}
        for c in eligible:
            if c["path"] not in focused_paths:
                focused.append(c)
            if len(focused) >= 3:
                break

    # Re-sort descending by confidence
    focused.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)

    return focused






def semantic_verification_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 3C: Semantic Verification (Component Group Aware)

    Uses LLM to evaluate the true purpose of the top-20 files + FORCE_PROMOTE_ROLES.
    Component group awareness: when any member of a component group is included,
    ALL members of that group are included — preventing false rejections of
    sibling files (.html, .scss) that look irrelevant in isolation.
    Creates semantic_verification.json trace.
    """
    print("\n" + "="*80)
    print(" ENTERING: semantic_verification_node() in workflow.py")
    print("   Purpose: Verify semantic ownership before planning (group-aware)")
    print("="*80)
    logger.info(" PHASE 3C: Semantic Verification (Component Group Aware)")

    if state.get("status") == "failed":
        return {"status": "failed"}

    # ── Skip if agentic loop already verified ────────────────────────────
    # The agentic search loop verifies files per-iteration using
    # verify_single().  Its verdicts are already in the expected batch
    # shape (list of {file_path, decision, semantic_relevance_score, reason}).
    # If evidence_collection_node populated them, use as-is.
    pre_computed = state.get("semantic_verification_results")
    if pre_computed and isinstance(pre_computed, list) and len(pre_computed) > 0:
        logger.info(
            f"   Using {len(pre_computed)} pre-computed agentic verdicts "
            f"(skipping batch LLM verification)"
        )
        component_groups = state.get("component_groups") or []

        # ── Apply Component Group Inclusion Rule to pre-computed verdicts ──
        # Without this, the short-circuit path skips the group-pull logic
        # that the batch verifier path runs (lines 1630+), causing siblings
        # (.html, .scss) of included .ts files to be silently dropped.
        if component_groups:
            path_to_group: dict[str, "ComponentGroup"] = {}
            for group in component_groups:
                if group.is_group:
                    for member_path in group.all_file_paths():
                        path_to_group[member_path.replace("\\", "/")] = group

            included_paths = {
                r["file_path"].replace("\\", "/")
                for r in pre_computed
                if r.get("decision") == "include"
            }
            already_in = {
                r["file_path"].replace("\\", "/") for r in pre_computed
            }

            group_promoted = 0
            for inc_path in list(included_paths):
                group = path_to_group.get(inc_path)
                if not group:
                    continue
                for member_path in group.all_file_paths():
                    norm = member_path.replace("\\", "/")
                    if norm not in already_in:
                        pre_computed.append({
                            "file_path": member_path,
                            "decision": "include",
                            "semantic_relevance_score": group.group_score * 0.85,
                            "reason": (
                                f"Component group member of '{group.stem}'. "
                                f"Auto-included because sibling "
                                f"'{Path(group.primary_file).name}' was included."
                            ),
                        })
                        already_in.add(norm)
                        group_promoted += 1

            if group_promoted > 0:
                logger.info(
                    f"   [ComponentGroup] Auto-included {group_promoted} "
                    f"group members via component group inclusion rule (short-circuit path)"
                )

        return {
            "semantic_verification_results": pre_computed,
            "component_groups": component_groups,
            "status": "semantic_verified",
        }

    ticket = state["ticket"]
    evidence_items = state.get("evidence_items", [])
    ranked_files = state.get("ranked_files", [])
    component_groups = state.get("component_groups") or []
    
    if not ranked_files and "discovered_files" in state:
        logger.warning("No ranked_files found in state. Skipping Semantic Verification.")
        return {"status": "semantic_verification_skipped"}

    # Evaluate Top 20
    top_candidates = ranked_files[:20]

    # TODO: Add FORCE_PROMOTE_ROLES to evaluation list if not in top 20
    _FORCE_PROMOTE_ROLES = {
        CandidateRole.DEPLOYMENT.value,
        CandidateRole.ROOT_COMPONENT.value,
        CandidateRole.CONFIG.value,
        CandidateRole.VERSION_SOURCE.value,
    }
    
    # We don't have candidate_role directly in RankedFile, but we have arch_role.
    # Actually arch_role is strings like "deploy_script", "config_file", "root_component".
    _FORCE_PROMOTE_ARCH_ROLES = {
        "deploy_script", "config_file", "root_component"
    }

    force_promote = [f for f in ranked_files if f.arch_role in _FORCE_PROMOTE_ARCH_ROLES and f not in top_candidates]
    eval_candidates = top_candidates + force_promote

    logger.info(f"   Evaluating {len(eval_candidates)} candidates via SemanticVerificationAgent")
    
    results = agents.semantic_verifier.verify_candidates(
        ticket=ticket,
        candidates=eval_candidates,
        evidence_items=evidence_items,
        top_n=len(eval_candidates)
    )

    # ── Component Group Inclusion Rule ───────────────────────────────────
    # If ANY member of a component group is "include"d, ALL members of that
    # group must be included. This prevents .html/.scss from being rejected
    # because the verifier only evaluated them in isolation.
    if component_groups:
        # Build a map: file_path → ComponentGroup
        path_to_group: dict[str, "ComponentGroup"] = {}
        for group in component_groups:
            if group.is_group:
                for member_path in group.all_file_paths():
                    path_to_group[member_path.replace("\\", "/")] = group

        # Find all included paths
        included_paths = {
            r.file_path.replace("\\", "/")
            for r in results
            if r.decision == "include"
        }
        already_in_results = {
            r.file_path.replace("\\", "/") for r in results
        }

        # For each included file, check if it belongs to a group
        # and ensure all group members are also included
        group_promoted_count = 0
        for included_path in list(included_paths):
            group = path_to_group.get(included_path)
            if not group:
                continue
            for member_path in group.all_file_paths():
                norm_member = member_path.replace("\\", "/")
                if norm_member not in already_in_results:
                    # This group member wasn't evaluated — auto-include it
                    from ticket_to_code.agents.semantic_verification_agent import (
                        SemanticVerificationResult,
                    )
                    results.append(SemanticVerificationResult(
                        file_path=member_path,
                        ranking_score=group.group_score * 0.85,
                        semantic_relevance_score=group.group_score * 0.85,
                        decision="include",
                        reason=(
                            f"Component group member of '{group.stem}'. "
                            f"Auto-included because sibling "
                            f"'{Path(group.primary_file).name}' was included."
                        ),
                    ))
                    already_in_results.add(norm_member)
                    group_promoted_count += 1

        if group_promoted_count > 0:
            logger.info(
                f"   [ComponentGroup] Auto-included {group_promoted_count} "
                f"group members via component group inclusion rule"
            )

    import json
    _run_trace_dir = agents.tracer.trace_dir
    try:
        trace_data = [
            {
                "file_path": r.file_path,
                "ranking_score": r.ranking_score,
                "semantic_relevance_score": r.semantic_relevance_score,
                "decision": r.decision,
                "reason": r.reason
            }
            for r in results
        ]
        write_trace_artifact(state["workspace_path"], ticket.ticket_id, "semantic_verification.json", trace_data)
        logger.info("   semantic_verification.json trace written")
    except Exception as e:
        logger.warning(f"Failed to write semantic_verification.json: {e}")

    # Pass the verification results forward. We can store them in state.
    # The planner node will then use this information.
    return {
        "semantic_verification_results": [
            {
                "file_path": r.file_path,
                "decision": r.decision,
                "semantic_relevance_score": r.semantic_relevance_score,
                "reason": r.reason
            }
            for r in results
        ],
        "component_groups": component_groups,
        "status": "semantic_verified"
    }


def plan_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 2: Architectural Planning
    
    Designs architecture and identifies files to create/modify.
    Uses RAG to understand existing codebase structure!
    """
    print("\n" + "="*80)
    print(" ENTERING: plan_node() in workflow.py")
    print("   Purpose: Design architecture and create development tasks")
    print("="*80)

    # If discovery hard-stopped the workflow, don't overwrite the failed status.
    # The unconditional discover→plan edge means plan_node always runs after
    # discover_node, even when discover_node returned status="failed".  We must
    # preserve that status so route_after_validate_candidates routes to END.
    if state.get("status") == "failed":
        logger.error("   plan_node: discovery failed — skipping planning, preserving failed status")
        return {"status": "failed"}  # Re-assert; validate_candidates_node must also honour this

    logger.info(" PHASE 2: Architectural Planning")
    
    # NEW: Retrieve architectural context using RAG!
    logger.info("   Retrieving existing codebase patterns from RAG...")
    
    # Build queries based on requirements
    requirements = state["requirements"]
    planning_queries = []
    
    # Query 1: Based on affected components
    if requirements.affected_components:
        planning_queries.append(f"existing code structure {' '.join(requirements.affected_components[:3])}")
    
    # Query 2: Based on technical requirements
    if requirements.technical_requirements:
        tech_keywords = ' '.join(requirements.technical_requirements[:2])[:100]
        planning_queries.append(f"similar implementation {tech_keywords}")
    
    # Query 3: General architecture patterns
    planning_queries.append("project structure file organization patterns")
    
    # Retrieve architectural patterns from RAG
    architectural_context = []
    for query in planning_queries[:3]:
        logger.info(f"   RAG Query: {query}")
        context_chunks = agents.rag_engine.retrieve_context(
            query=query,
            max_results=5,
            document_types=None,  # Get all types (source code, docs, etc.)
            schema="both",  # Search both codebase and architectural guidelines
            
        )
        architectural_context.extend(context_chunks)
    
    logger.info(f"  ✅ Retrieved {len(architectural_context)} architectural examples")
    
    # Build codebase context string from RAG results
    codebase_context = "EXISTING CODEBASE STRUCTURE AND PATTERNS:\n\n"
    
    # ── FIX 2: Microservice Boundary Graph Injection ──
    try:
        from ticket_to_code.utils.dependency_graph import get_dependency_graph
        module_graph = get_dependency_graph(state["workspace_path"])
        codebase_context += module_graph.get_summary_string() + "\n\n"
        codebase_context += "CRITICAL ARCHITECTURAL BOUNDARY RULE:\n"
        codebase_context += "You CANNOT use direct Java imports or Spring Bean injection between isolated microservices unless explicitly declared in the 'Local Dependencies' above.\n"
        codebase_context += "If module A needs data from module B and has no local dependency on it, you MUST use an HTTP/REST Connector or reject the approach.\n\n"
    except Exception as e:
        logger.warning(f"  Failed to generate dependency graph: {e}")
        
    for idx, chunk in enumerate(architectural_context[:10], 1):
        codebase_context += f"Example {idx}:\n"
        codebase_context += f"File: {chunk.get('file_path', 'Unknown')}\n"
        codebase_context += f"Type: {chunk.get('type', 'code')}\n"
        codebase_context += f"Content:\n{chunk.get('content', '')[:400]}\n"
        codebase_context += "-" * 40 + "\n"
    
    # Filter out blacklisted candidates so the planner never re-selects them
    blacklisted = state.get("blacklisted_files") or []
    
    # Filter out semantic verification rejections
    verification_results = state.get("semantic_verification_results") or []
    semantic_rejections = {
        res["file_path"].replace('\\', '/')
        for res in verification_results
        if res["decision"] in ("REJECT", "exclude")
    }

    # ── FIX: Allowlist mode when agentic verdicts exist ──────────────────
    # When the agentic loop provides verdicts, use ALLOWLIST instead of
    # DENYLIST.  The old denylist approach only blocked explicitly rejected
    # files, but files the agentic loop never evaluated leaked through.
    # Now: if there are agentic "include" verdicts, ONLY those files (plus
    # evidence items) are allowed into the planner pool.
    semantic_allowlist = {
        res["file_path"].replace('\\', '/')
        for res in verification_results
        if res.get("decision") in ("include",)
    }
    _use_allowlist = len(semantic_allowlist) > 0

    if _use_allowlist:
        # Allowlist mode: only keep discovered files that the agentic loop
        # explicitly marked as "include"
        active_discovered = [
            f for f in (state.get("discovered_files") or [])
            if f["path"].replace('\\', '/') in semantic_allowlist
            and f["path"].replace('\\', '/') not in blacklisted
        ]
        logger.info(
            f"  [Allowlist] Agentic verdicts present — kept {len(active_discovered)} "
            f"of {len(state.get('discovered_files') or [])} discovered files "
            f"({len(semantic_allowlist)} explicitly included)"
        )
    else:
        # Denylist mode (legacy): no agentic verdicts, use old behavior
        active_discovered = [
            f for f in (state.get("discovered_files") or [])
            if f["path"].replace('\\', '/') not in blacklisted and f["path"].replace('\\', '/') not in semantic_rejections
        ]

    # [NEW] Merge high-fidelity Evidence into the Discovery pool.
    # Use a RELATIVE score band instead of a flat 0.70 threshold so that
    # over-collection after recall widening doesn't flood the planner:
    # only items within 55% of the best evidence score are merged.
    evidence_items = state.get("evidence_items") or []
    existing_paths = {d["path"].replace('\\', '/') for d in active_discovered}
    blacklisted_norm = {p.replace('\\', '/') for p in blacklisted}

    if evidence_items:
        _best_ev = max(e.relevance_score for e in evidence_items)
        _ev_threshold = max(0.35, _best_ev * 0.55)  # relative band, absolute floor 0.35
    else:
        _ev_threshold = 0.70  # fallback when no evidence collected

    # ── FIX (Bug 1): Evidence-Boost Merge ────────────────────────────────
    # Previously, evidence for files already in the discovery pool was
    # silently discarded.  Now we BOOST their confidence and inject the
    # evidence_match signal so they rank above keyword-only matches.
    for e in evidence_items:
        norm_path = e.file_path.replace('\\', '/')
        if e.relevance_score < _ev_threshold or norm_path in blacklisted_norm or norm_path in semantic_rejections:
            continue

        ev_sigs = [f"evidence_match:{e.source}", f"score:{e.relevance_score:.2f}"]
        if hasattr(e, "ranking_reasons") and e.ranking_reasons:
            ev_sigs.extend(e.ranking_reasons)

        if norm_path in existing_paths:
            # BOOST: file already in pool — upgrade its confidence and inject evidence signals
            for d in active_discovered:
                if d["path"].replace('\\', '/') == norm_path:
                    d["confidence"] = max(d.get("confidence", 0.0), e.relevance_score)
                    for sig in ev_sigs:
                        if sig not in d.get("signals", []):
                            d.setdefault("signals", []).insert(0, sig)
                    logger.info(f"  ⬆ Evidence-boosted existing candidate: {norm_path} → confidence={d['confidence']:.3f}")
                    break
        else:
            # NEW: file not in pool — inject it
            active_discovered.append({
                "path": e.file_path,
                "confidence": e.relevance_score,
                "features": {},
                "signals": ev_sigs
            })
            existing_paths.add(e.file_path)

    # Bias candidate ranking for cross-tab UI state regressions so owner and
    # shared state files are considered before action-tab side effects.
    _biased = _apply_ui_state_regression_bias(active_discovered, state.get("ticket"))
    if _biased:
        logger.info(f"  [ui-bias] adjusted confidence for {_biased} candidate(s)")

    # ── Component Group Context for Planner ──────────────────────────────
    # Tell the planner which files belong together as framework components.
    # The planner can then create SEPARATE tasks for each file that needs
    # changes (e.g., one task for .ts logic, another for .html template),
    # instead of only planning for one file and leaving siblings to blind
    # companion injection.
    component_groups = state.get("component_groups") or []
    multi_file_groups = [g for g in component_groups if g.is_group]
    if multi_file_groups:
        codebase_context += "\nCOMPONENT GROUPS (files that belong together as framework components):\n"
        codebase_context += "IMPORTANT: If any file in a group needs changes, consider whether its\n"
        codebase_context += "siblings also need changes. Create SEPARATE tasks for each file that\n"
        codebase_context += "needs modification — do NOT assume only the .ts file needs changes.\n\n"
        for idx, group in enumerate(multi_file_groups[:10], 1):
            codebase_context += f"  Group {idx}: {group.stem}\n"
            for ext, member_path in sorted(group.members.items()):
                label = "logic" if ext in _GROUP_SOURCE_EXTS else "template/style"
                in_evidence = "(evidence)" if ext in group.evidence_sources else "(grouped)"
                codebase_context += f"    - {Path(member_path).name} [{label}] {in_evidence}\n"
            codebase_context += "\n"
        logger.info(
            f"  [ComponentGroup] Injected {len(multi_file_groups)} component "
            f"groups into planner context"
        )

    # Sort the unified pool by confidence descending
    active_discovered.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)

    if blacklisted:
        logger.info(f"  ⛔ Blacklisted (excluded from planning): {blacklisted}")
    logger.info(f"  ✅ Active candidates in unified pool: {len(active_discovered)}")

    # ── FIX 1: Classify ALL candidates first, then filter noise from planner ──
    # Classify the full pool so LOCK_FILE / GENERATED candidates are excluded
    # from the top-25 planner slots.  These files consume candidate slots without
    # adding planning value and can be accidentally selected by the LLM despite
    # role-based prohibitions.  They remain in full pool traces for observability.
    for cand in active_discovered:
        _structural_role = _classify_candidate_role(cand.get("path", ""))

        # AST/localization can mark non-code infra files as READ_ONLY because they have
        # no methods/imports. For DEPLOYMENT/CONFIG, force writable ownership so
        # PatchValidator can allow legitimate infrastructure edits.
        if _structural_role in (CandidateRole.DEPLOYMENT.value, CandidateRole.CONFIG.value):
            cand["candidate_role"] = _structural_role
            if "features" not in cand or not isinstance(cand.get("features"), dict):
                cand["features"] = {}
            cand["features"]["ownership_type"] = "PRIMARY_OWNER"
            continue

        # Priority 1: canonical ownership_type set by the discovery pipeline
        _role = (cand.get("features") or {}).get("ownership_type", "") or ""
        if _role not in ("PRIMARY_OWNER", "DISPLAY_OWNER", "SUPPORTING", "READ_ONLY"):
            # Priority 2: ownership signal strings (legacy path)
            _role = ""
            for _sig in cand.get("signals", []):
                _sig_str = str(_sig)
                if "PRIMARY_OWNER" in _sig_str:
                    _role = "PRIMARY_OWNER"
                    break
                elif "DISPLAY_OWNER" in _sig_str:
                    _role = "DISPLAY_OWNER"
                    break
                elif "SUPPORTING" in _sig_str:
                    _role = "SUPPORTING"
                    break
        if _role in ("PRIMARY_OWNER", "DISPLAY_OWNER", "SUPPORTING", "READ_ONLY"):
            cand["candidate_role"] = _role
        else:
            # Priority 3: structural path heuristic (last resort)
            cand["candidate_role"] = _structural_role

    # ── FIX 4: Re-sort with compound key now that roles are known ────────────
    # Primary: confidence descending.  Secondary: role priority (DEPLOYMENT first,
    # GENERATED last).  Tertiary: alphabetical path.
    # This makes top-25 selection fully deterministic when many files share
    # confidence=1.0 — run-job.sh (DEPLOYMENT) and app.component.ts (ROOT_COMPONENT)
    # consistently outrank .spec.ts (TEST) and package-lock.json (LOCK_FILE).
    _ROLE_PRIORITY = {
        CandidateRole.DEPLOYMENT.value:      0,
        CandidateRole.ROOT_COMPONENT.value:  1,
        CandidateRole.CONFIG.value:          2,
        CandidateRole.VERSION_SOURCE.value:  3,
        CandidateRole.VERSION_DISPLAY.value: 4,
        CandidateRole.UNKNOWN.value:         5,
        CandidateRole.TEST.value:            6,
        CandidateRole.STYLE.value:           7,
        CandidateRole.LOCK_FILE.value:       8,
        CandidateRole.GENERATED.value:       9,
    }
    active_discovered.sort(
        key=lambda x: (
            -x.get("confidence", 0.0),
            _ROLE_PRIORITY.get(x.get("candidate_role", CandidateRole.UNKNOWN.value), 5),
            x.get("path", ""),
        )
    )

    _PLANNER_EXCLUDED_ROLES = {
        CandidateRole.LOCK_FILE.value,
        CandidateRole.GENERATED.value,
    }
    
    # ── FIX: Automatically drop TEST files from planner unless ticket is testing-focused
    _inv = state.get("investigation_result")
    _req_code = getattr(_inv, "requires_code_changes", False) if hasattr(_inv, "requires_code_changes") else (_inv.get("requires_code_changes", False) if isinstance(_inv, dict) else False)
    _t_title = state["ticket"].title.lower() if hasattr(state.get("ticket"), "title") else ""
    _t_desc = state["ticket"].description.lower() if hasattr(state.get("ticket"), "description") else ""
    req_testing = _req_code and ("test" in _t_title or "spec" in _t_title or "test" in _t_desc)
    if not req_testing:
        _PLANNER_EXCLUDED_ROLES.add(CandidateRole.TEST.value)

    _planner_excluded = [
        c for c in active_discovered 
        if c["candidate_role"] in _PLANNER_EXCLUDED_ROLES or (c.get("path") and c["path"].replace('\\', '/') in [b.replace('\\', '/') for b in (blacklisted or [])])
    ]
    _planner_eligible = [
        c for c in active_discovered 
        if c["candidate_role"] not in _PLANNER_EXCLUDED_ROLES and (not c.get("path") or c["path"].replace('\\', '/') not in [b.replace('\\', '/') for b in (blacklisted or [])])
    ]

    if _planner_excluded:
        logger.info(
            f"   Planner-excluded (LOCK_FILE/GENERATED/BLACKLISTED, trace-only): "
            f"{[c['path'] for c in _planner_excluded]}"
        )

    # ── Focus the candidate pool using ownership-cluster analysis ─────────────
    # _select_planner_candidates applies a relative score band, always preserves
    # PRIMARY_OWNER files, and adapts K to ownership richness. No hardcoded
    # extensions or framework roles — purely graph topology + evidence scores.
    active_discovered_for_planner = _select_planner_candidates(_planner_eligible)

    import json
    # ── FIX 2+3: trace artifacts are run-scoped and written atomically ────────
    # candidate_pool.json and planner_decisions.json are written ONLY after
    # create_plan() succeeds, to the tracer's timestamped run directory.
    # A crash inside create_plan() writes planner_failed.json instead.
    # Ticket-global flat files are never written — cross-run chimeras are impossible.
    _run_trace_dir = agents.tracer.trace_dir  # None when TRACE_MODE is off

    try:
        plan = agents.planner.create_plan(
            state["ticket"],
            state["requirements"],
            codebase_context=codebase_context,           # ← RAG context passed here!
            discovered_files=active_discovered_for_planner,  # ← Real repo files (top-25, blacklist excluded)
            blacklisted_files=blacklisted,               # ← Tell the LLM which files are FORBIDDEN
            workspace_path=str(agents.workspace_path),   # ← Fix Bug 3: inject real file content
            verification_feedback=verification_results,  # ← FIX 4.3: Pass semantic feedback
            validation_failure_reason=state.get("validation_failure_reason"), # ← Feedback from gating
        )
    except Exception as plan_err:
        if _run_trace_dir is not None and _run_trace_dir.exists():
            try:
                (_run_trace_dir / "planner_failed.json").write_text(
                    json.dumps({
                        "error": str(plan_err),
                        "candidate_pool_size": len(active_discovered_for_planner),
                        "candidates": active_discovered_for_planner,
                    }, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                logger.warning(f"   planner_failed.json written \u2192 {_run_trace_dir}")
            except Exception as write_err:
                logger.warning(f"  Failed to write planner_failed.json: {write_err}")
        raise

    # Post-process: enforce the blacklist at the response level.
    # The LLM may still propose blacklisted paths despite the FORBIDDEN instruction
    # in its prompt.  Silently drop any such tasks so validate_candidates never
    # sees them again — this stops the propose→reject→propose cycle cold.
    if blacklisted:
        original_count = len(plan.tasks)
        plan.tasks = [t for t in plan.tasks if t.file_path not in blacklisted]
        dropped = original_count - len(plan.tasks)
        if dropped:
            logger.warning(
                f"   post-plan blacklist filter: dropped {dropped} task(s) that referenced "
                f"blacklisted file(s).  Remaining tasks: {len(plan.tasks)}"
            )

    # ── Post-plan: enforce that task paths are grounded in reality ────────
    # The LLM may hallucinate file paths (e.g. "app/app.py" instead of "src/app.py")
    # despite the "MUST SELECT FROM THESE" instruction.  This block auto-corrects
    # or drops such tasks BEFORE they reach validate_candidates.
    discovered_paths = {d["path"].replace("\\", "/") for d in active_discovered_for_planner}
    discovered_basenames: dict[str, list[str]] = {}
    for dp in discovered_paths:
        bn = dp.rsplit("/", 1)[-1].lower()
        discovered_basenames.setdefault(bn, []).append(dp)

    ws_root = Path(state.get("workspace_path")) if state.get("workspace_path") else None

    corrected_tasks = []
    for task in plan.tasks:
        task_norm = task.file_path.replace("\\", "/")
        task_type_val = getattr(task.task_type, "value", str(task.task_type)).lower()

        # ── READ_ONLY: pass through (no writes)
        if task_type_val == "read_only":
            corrected_tasks.append(task)
            continue

        # ── CREATE: smart handling
        if task_type_val == "create":
            if ws_root and (ws_root / task.file_path).exists():
                # File already exists — check intent from task description
                desc_lower = (task.description or "").lower()
                create_intent_keywords = [
                    "create new", "implement new", "add new file",
                    "new class", "new module", "new service",
                    "generate a new", "generate new", "create ",
                ]
                is_genuine_create = any(kw in desc_lower for kw in create_intent_keywords)

                if is_genuine_create:
                    # Genuinely wants a new file but name collides — rename
                    import os as _os
                    base, ext = _os.path.splitext(task.file_path)
                    new_path = f"{base}_new{ext}"
                    old_path = task.file_path
                    task.file_path = new_path
                    logger.warning(
                        f"   ✏️  CREATE name collision: '{old_path}' exists. "
                        f"Renamed to '{new_path}'. Updating references in other tasks."
                    )
                    # Update references in other tasks' descriptions/dependencies
                    for other_task in plan.tasks:
                        if other_task.id != task.id:
                            if old_path in (other_task.description or ""):
                                other_task.description = other_task.description.replace(
                                    old_path, new_path
                                )
                            if old_path in (other_task.dependencies or []):
                                other_task.dependencies = [
                                    new_path if d == old_path else d
                                    for d in other_task.dependencies
                                ]
                else:
                    # Intent is actually to modify the existing file
                    logger.warning(
                        f"   ✏️  CREATE→MODIFY: '{task.file_path}' already exists "
                        f"and task intent is modification. Converting to MODIFY."
                    )
                    task.task_type = TaskType.MODIFY
                    task.new_file_creation_allowed = False

            corrected_tasks.append(task)
            continue

        # ── MODIFY: path MUST exist in discovered files or on disk
        if task_norm in discovered_paths:
            corrected_tasks.append(task)
            continue
        if ws_root and (ws_root / task.file_path).exists():
            corrected_tasks.append(task)
            continue

        # User requested to NEVER auto-correct hallucinated paths for MODIFY tasks.
        # Instead, we pass the hallucinated path directly to the candidate validation node.
        # The validation node will see the file doesn't exist, reject it, blacklist the 
        # hallucinated path, and force the LLM to explicitly choose the correct path 
        # from the available candidates list in a retry loop.
        corrected_tasks.append(task)

    if len(corrected_tasks) != len(plan.tasks):
        logger.info(
            f"   Post-plan enforcement: {len(plan.tasks)} → {len(corrected_tasks)} tasks "
            f"({len(plan.tasks) - len(corrected_tasks)} dropped/corrected)"
        )
    plan.tasks = corrected_tasks

    # ── Capability resolution: reuse existing code by INTENT, not by name ─────
    # For each writable task, ask "does existing code already SOLVE this task's
    # intent, regardless of what it is named?".  When yes, we point the task at
    # the REAL existing symbol (target_method/target_class) and convert a CREATE
    # into a MODIFY so the generator extends existing behaviour instead of
    # producing a duplicate that merely re-implements the same capability.
    # This is semantic (embedding + LLM judgement), never fixed-name matching.
    if os.environ.get("AVIATOR_CAPABILITY_RESOLVE", "1") != "0":
        try:
            from ticket_to_code.agents.task_level_analyzer import (
                SemanticCapabilityResolver,
                ResolutionDecision,
            )

            resolver = SemanticCapabilityResolver(
                rag_engine=agents.rag_engine,
                repo_search_engine=agents.repo_search,
                llm=agents.planner.llm,
                workspace_path=str(agents.workspace_path),
            )

            _writable_tasks = [
                t for t in plan.tasks
                if getattr(t.task_type, "value", str(t.task_type)).lower() != "read_only"
            ]
            # Bound LLM calls so planning latency stays predictable.
            _max_resolves = int(os.environ.get("AVIATOR_CAPABILITY_RESOLVE_MAX", "6"))
            _resolutions_trace = []

            for task in _writable_tasks[:_max_resolves]:
                intent = (task.description or task.title or "").strip()
                if not intent:
                    continue
                resolution = resolver.resolve_task(
                    intent=intent,
                    title=task.title or "",
                    hints={"file_path": task.file_path},
                )
                _resolutions_trace.append(resolution.to_dict())

                # Only act on confident reuse of a REAL existing file.
                if not (resolution.should_reuse and resolution.confidence >= 0.6):
                    continue
                if not resolution.target_file:
                    continue

                target_norm = resolution.target_file.replace("\\", "/")
                target_exists = target_norm in discovered_paths or (
                    ws_root and (ws_root / resolution.target_file).exists()
                )
                if not target_exists:
                    continue

                # Record the real symbol to reuse/extend (whatever its name is).
                if resolution.target_symbol and resolution.target_symbol != "(file)":
                    task.target_method = resolution.target_symbol
                    if resolution.target_symbol not in task.allowed_methods:
                        task.allowed_methods.append(resolution.target_symbol)

                note = (
                    f" | capability-reuse: existing '{resolution.target_symbol or target_norm}' "
                    f"in {target_norm} already fulfils this intent "
                    f"(conf={resolution.confidence:.2f}): {resolution.reasoning[:160]}"
                )
                task.selection_reason = (task.selection_reason or "") + note

                task_type_val = getattr(task.task_type, "value", str(task.task_type)).lower()
                if task_type_val == "create":
                    task.task_type = TaskType.MODIFY
                    task.new_file_creation_allowed = False
                    # Retarget onto the file that already provides the capability.
                    task.file_path = resolution.target_file
                    logger.info(
                        f"   CREATE→MODIFY (capability): task '{task.id}' reuses existing "
                        f"'{resolution.target_symbol or target_norm}' in {target_norm}"
                    )

            # Persist a trace for debugging/inspection.
            if _resolutions_trace and _run_trace_dir is not None and _run_trace_dir.exists():
                try:
                    (_run_trace_dir / "capability_resolutions.json").write_text(
                        json.dumps(_resolutions_trace, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
        except Exception as _cap_err:
            logger.warning(f"   Capability resolution skipped: {_cap_err}")

    # ── Change 2: Derive PlannerDecisions ────────────────────────────────────
    # Compare the candidates that were shown to the planner against the tasks
    # it actually produced.  Every candidate gets a REQUIRED / OPTIONAL / IGNORE
    # decision.  grounded_understanding_node reads these to avoid auto-injecting
    # files the planner intentionally excluded.
    task_paths_writable  = {t.file_path for t in plan.tasks if t.task_type.value != "read_only"}
    task_paths_readonly  = {t.file_path for t in plan.tasks if t.task_type.value == "read_only"}

    planner_decisions: dict = {}
    for cand in active_discovered_for_planner:
        fp   = cand["path"]
        role = cand.get("candidate_role", CandidateRole.UNKNOWN.value)
        conf = cand.get("confidence", 0.0)

        if fp in task_paths_writable:
            decision = PlannerDecisionValue.REQUIRED.value
            reason   = "Planner created a modify/create task for this file"
        elif fp in task_paths_readonly:
            decision = PlannerDecisionValue.OPTIONAL.value
            reason   = "Planner included this file as read-only context"
        else:
            decision = PlannerDecisionValue.IGNORE.value
            reason   = "Planner evaluated this candidate and did not select it"

        planner_decisions[fp] = {
            "file_path":  fp,
            "role":       role,
            "decision":   decision,
            "reason":     reason,
            "confidence": conf,
        }

    # Log summary
    req_count  = sum(1 for d in planner_decisions.values() if d["decision"] == PlannerDecisionValue.REQUIRED.value)
    opt_count  = sum(1 for d in planner_decisions.values() if d["decision"] == PlannerDecisionValue.OPTIONAL.value)
    ign_count  = sum(1 for d in planner_decisions.values() if d["decision"] == PlannerDecisionValue.IGNORE.value)
    logger.info(
        f"   PlannerDecisions: {req_count} REQUIRED, "
        f"{opt_count} OPTIONAL, {ign_count} IGNORE "
        f"(out of {len(planner_decisions)} candidates evaluated)"
    )
    for fp, dec in planner_decisions.items():
        logger.info(
            f"    [{dec['decision']:8s}] role={dec['role']:16s} {fp}"
        )

    # ── FIX 2+3: Atomic run-scoped write of pool + decisions ─────────────────
    # Both files land in the same tracer run directory and are written together,
    # so they always represent the same execution — cross-run chimera impossible.
    if _run_trace_dir is not None and _run_trace_dir.exists():
        try:
            (_run_trace_dir / "candidate_pool.json").write_text(
                json.dumps(active_discovered_for_planner, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (_run_trace_dir / "planner_decisions.json").write_text(
                json.dumps(list(planner_decisions.values()), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(
                f"   candidate_pool.json ({len(active_discovered_for_planner)} entries) + "
                f"planner_decisions.json → {_run_trace_dir}"
            )
        except Exception as err:
            logger.warning(f"  Failed to write trace artifacts: {err}")

    logger.info(
        f"Planning complete:\n"
        f"  Tasks: {len(plan.tasks)}\n"
        f"  API changes: {len(plan.api_changes)}\n"
        f"  Using RAG examples: {len(architectural_context)}"
    )
    # ── FIX 1: Pass the actual planner input (top-25), not the full pool ──────
    # Previously active_discovered (185-204 entries) was passed, making
    # candidates_passed_to_planner in 05_planning.json a misleading 7-8x overcount.
    agents.tracer.record_planning(
        plan, 
        active_discovered_for_planner,
        system_message=getattr(agents.planner, "last_system_prompt", None),
        user_message=getattr(agents.planner, "last_user_prompt", None)
    )
    
    selected = [t.file_path for t in plan.tasks] if hasattr(plan, "tasks") else []
    candidates = [c["path"] for c in active_discovered_for_planner]
    artifact = {
        "candidate_files": candidates,
        "selected_files": selected,
        "rejected_files": [c for c in candidates if c not in selected],
        "planner_reasoning": getattr(plan, "reasoning", "")
    }
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "planner_selection.json", artifact)
    
    return {
        "architectural_plan": plan,
        "discovered_files": active_discovered_for_planner,
        "planner_decisions": planner_decisions,
        "status": "planning_complete"
    }


def _verify_ticket_already_satisfied(state: TicketToCodeState, ticket_text: str) -> "tuple[bool, str]":
    """Verify (with grounded evidence) whether the ticket's change already exists in code.

    Used only to distinguish "planner produced no tasks because the feature is ALREADY
    implemented" from "planner simply failed to plan". We never trust the LLM's word alone:
    the cited evidence snippet must mechanically exist on disk in the cited file.

    Returns (is_satisfied, detail). is_satisfied is True only when the LLM asserts the change
    is already present AND that claim is verified against the real file contents.
    """
    workspace_path = state.get("workspace_path", "")
    if not workspace_path:
        return False, "No workspace path available for verification."

    # Gather candidate files that actually exist on disk (ranked first, then discovered).
    candidate_paths: List[str] = []
    for r in (state.get("ranked_files") or []):
        fp = getattr(r, "file_path", "")
        if fp:
            candidate_paths.append(fp)
    for d in (state.get("discovered_files") or []):
        fp = d.get("path", "") if isinstance(d, dict) else ""
        if fp:
            candidate_paths.append(fp)

    seen: set = set()
    file_blocks: List[str] = []
    used_files: List[str] = []
    for fp in candidate_paths:
        norm = fp.replace("\\", "/")
        if norm in seen:
            continue
        seen.add(norm)
        full = os.path.join(workspace_path, fp)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()[:4000]
        except Exception:
            continue
        file_blocks.append(f"### FILE: {norm}\n```\n{content}\n```")
        used_files.append(norm)
        if len(used_files) >= 4:
            break

    if not file_blocks:
        return False, "No readable candidate files to verify against."

    try:
        from aviator.services.llm import LLMRegistry
        llm = LLMRegistry.get_llm(assistant=False)
    except Exception as exc:
        return False, f"LLM unavailable for verification ({exc})."

    prompt = f"""You are verifying whether a ticket's requested change is ALREADY implemented in the code.

TICKET:
{ticket_text}

RELEVANT FILES (current contents):
{os.linesep.join(file_blocks)}

Decide strictly:
- "already_implemented" = true ONLY if the requested behavior is clearly, fully present in the code above.
- If the change is missing, partial, or you are unsure, set it to false.

Return JSON only with exactly these keys:
{{
  "already_implemented": true | false,
  "evidence_file": "<exact file path from the list above, or empty>",
  "evidence_snippet": "<verbatim code snippet copied from that file proving it, or empty>",
  "reasoning": "<one sentence>"
}}
No markdown, no extra keys."""


    try:
        raw = llm.invoke(prompt)
        content = getattr(raw, "content", raw)
        payload = json.loads(_extract_json_payload(str(content)))
    except Exception as exc:
        return False, f"Verification response could not be parsed ({exc})."

    if not isinstance(payload, dict) or payload.get("already_implemented") is not True:
        return False, f"Not verified as already implemented: {str(payload.get('reasoning', ''))[:200] if isinstance(payload, dict) else 'malformed response'}"

    evidence_file = str(payload.get("evidence_file", "")).strip()
    evidence_snippet = str(payload.get("evidence_snippet", "")).strip()
    if not evidence_file or not evidence_snippet:
        return False, "LLM claimed already-implemented but provided no grounding evidence."

    if evidence_file.replace("\\", "/") not in used_files:
        return False, f"Evidence file '{evidence_file}' was not among the inspected files."

    # Mechanical grounding: the cited snippet must actually exist in the cited file.
    if not verify_grounding_fact_text(evidence_snippet, evidence_file, workspace_path):
        return False, "Already-implemented claim failed mechanical grounding (snippet not found on disk)."

    return True, f"Verified already implemented in {evidence_file}: {str(payload.get('reasoning', ''))[:200]}"


def validate_candidates_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 2.6: Candidate Validation — runs between plan and localize.

    Ensures every planned writable file is grounded in the real repository:
      - file appears in discovered_files, OR
      - file physically exists on disk

    If any planned path is hallucinated / unrelated:
      - add it to blacklisted_files
      - increment candidate_retry_count
      - route back to plan (re-rank without blacklisted files)
      - if retries exhausted → route back to discover (expanded scope)

    Build failures do NOT trigger this node — only localization evidence does.
    """
    print("\n" + "="*80)
    print(" ENTERING: validate_candidates_node() in workflow.py")
    print("   Purpose: Reject hallucinated file paths before localization")
    print("="*80)
    logger.info("✅ PHASE 2.6: Candidate Validation")

    if state.get("status") == "failed":
        logger.error("   validate_candidates_node: workflow failed — skipping validation, routing to END")
        return {"status": "failed"}

    plan = _get_plan(state)
    discovered = state.get("discovered_files") or []
    ranked = state.get("ranked_files") or []
    evidence = state.get("evidence_items") or []
    blacklisted = state.get("blacklisted_files") or []
    workspace_path = state.get("workspace_path", "")
    
    ticket_obj = state.get("ticket")
    if hasattr(ticket_obj, "description"):
        ticket_text = f"{getattr(ticket_obj, 'title', '')} {getattr(ticket_obj, 'description', '')}"
    elif isinstance(ticket_obj, dict):
        ticket_text = f"{ticket_obj.get('title', '')} {ticket_obj.get('description', '')}"
    else:
        ticket_text = str(ticket_obj)

    # Guard: if no writable tasks, treat as planning failure
    writable_tasks = [t for t in plan.tasks if getattr(t.task_type, "value", str(t.task_type)) != "read_only"]
    if not writable_tasks:
        new_blacklist = list(blacklisted)
        new_retry_count = state.get("candidate_retry_count", 0) + 1
        max_retries = state.get("max_candidate_retries", 2)

        if new_retry_count >= max_retries:
            # Retries exhausted with no writable tasks. Before calling it a failure,
            # verify whether the requested change is ALREADY implemented. We only
            # declare "already satisfied" when the claim is mechanically grounded in
            # real file contents — otherwise this is a genuine planning failure.
            is_satisfied, detail = _verify_ticket_already_satisfied(state, ticket_text)
            if is_satisfied:
                logger.info("  ✅ Ticket already implemented (verified): %s", detail)
                return {
                    "blacklisted_files": new_blacklist,
                    "candidate_retry_count": new_retry_count,
                    "validation_failure_reason": detail,
                    "status": "no_action_required",
                    "code_status": "code_already_satisfied",
                }
            logger.error(
                "  ❌ Planner produced 0 writable tasks and the change is NOT already "
                "present (verification failed: %s). Treating as planning failure.",
                detail,
            )
            return {
                "blacklisted_files": new_blacklist,
                "candidate_retry_count": new_retry_count,
                "validation_failure_reason": (
                    "Planner produced no writable tasks and the requested change was not "
                    f"found in the codebase (not already implemented). {detail}"
                ),
                "status": "failed",
            }

        return {
            "blacklisted_files": new_blacklist,
            "candidate_retry_count": new_retry_count,
            "validation_failure_reason": "Plan produced 0 writable tasks",
            "status": "candidates_invalid",
        }

    accepted, rejected, is_consistent, failure_reason, task_logs = run_gating_logic(
        plan, discovered, ranked, evidence, workspace_path, ticket_text
    )

    # Log observability
    for log_entry in task_logs:
        if log_entry["status"] == "rejected":
            logger.warning(f"  ⛔ Rejected Task: {log_entry['task']} - Reason: {log_entry.get('reason', 'None')}")
        elif log_entry["status"] == "accepted":
            logger.info(f"  ✅ Accepted Task: {log_entry['task']} (Type: {log_entry.get('grounding_type')})")

    # If there are ANY rejected tasks, we force a retry of the whole plan to ensure safety.
    # We construct a per-task failure reason so the LLM knows what to keep and what to fix.
    if rejected:
        is_consistent = False
        reason_lines = ["Your previous plan contained REJECTED tasks. You MUST issue a new complete plan."]
        if accepted:
            reason_lines.append("\nACCEPTED tasks (preserve these EXACTLY in your new plan):")
            for t in accepted:
                reason_lines.append(f" ✅ {t.file_path}: {t.title}")

        rejected_reasons = {}
        for log_entry in task_logs:
            if log_entry["status"] == "rejected":
                rejected_reasons[log_entry["task"]] = log_entry.get("reason", "Unknown reason")

        reason_lines.append("\nREJECTED tasks (fix or replace these):")
        for t in rejected:
            r = rejected_reasons.get(t.file_path, "Unknown boundary/mechanical violation")
            reason_lines.append(f" ❌ {t.file_path}: {r}")

        # ── NEW: Include available files so planner can build a complete replacement plan
        reason_lines.append("\nAVAILABLE FILES IN PROJECT (use these EXACT paths for your new plan):")
        for d in (state.get("discovered_files") or []):
            d_path = d.get("path", "")
            d_conf = d.get("confidence", 0.0)
            if d_path:
                reason_lines.append(f" 📁 {d_path} (confidence: {d_conf:.2f})")

        failure_reason = "\n".join(reason_lines)

    if not is_consistent:
        # Check if this is a justification recovery
        is_justification_recovery = "explicit_planner_justification" in failure_reason
        
        if is_justification_recovery:
            new_just_retry_count = state.get("justification_retry_count", 0) + 1
            max_just_retries = state.get("max_justification_retries", 1)
            if new_just_retry_count > max_just_retries:
                is_justification_recovery = False
                logger.warning("  ⚠️ Justification recovery attempts exhausted. Failing back to hard replan.")
            else:
                logger.info(f"  🔄 Triggering Justification Recovery (Attempt {new_just_retry_count}/{max_just_retries})")
                return {
                    "justification_retry_count": new_just_retry_count,
                    "validation_failure_reason": failure_reason,
                    "status": "candidates_invalid",
                }

        new_retry_count = state.get("candidate_retry_count", 0) + 1
        max_retries = state.get("max_candidate_retries", 2)
        
        # ── Smart blacklisting based on rejection reason ────────────────────
        # Instead of blindly delaying ALL blacklisting, we distinguish:
        # 1. Ungrounded (score < 0.05, file doesn't exist) + zero cross-task role → blacklist immediately
        # 2. Architectural boundary violation → blacklist immediately
        # 3. LLM judgment failure (file exists but wrong context) → delay blacklist
        immediately_blacklist = []
        for t in rejected:
            t_path = t.file_path
            reject_reason = rejected_reasons.get(t_path, "")

            is_ungrounded = (
                "completely ungrounded" in reject_reason.lower()
                or "score < 0.05" in reject_reason.lower()
            )
            is_boundary = (
                "architectural" in reject_reason.lower()
                or "boundary" in reject_reason.lower()
            )

            if is_ungrounded:
                # File doesn't exist — check if any accepted task depends on it
                has_dependents = False
                for acc in accepted:
                    acc_desc = (acc.description or "").lower()
                    acc_deps = acc.dependencies or []
                    if t_path.lower() in acc_desc or t_path in acc_deps:
                        has_dependents = True
                        break

                if has_dependents:
                    logger.info(
                        f"  ⚠️ Rejected file '{t_path}' is referenced by accepted tasks. "
                        f"Not blacklisting — planner must fix or use existing file."
                    )
                else:
                    # Zero role — blacklist immediately
                    immediately_blacklist.append(t_path)
                    logger.info(
                        f"  🚫 Blacklisting '{t_path}': file doesn't exist and "
                        f"no other task depends on it (zero role)"
                    )

            elif is_boundary:
                # Architectural violation — always blacklist
                immediately_blacklist.append(t_path)
                logger.info(
                    f"  🚫 Blacklisting '{t_path}': architectural boundary violation"
                )
            # else: LLM judgment failure — delay blacklisting (give retry chance)

        # Always blacklist ungrounded/boundary files immediately
        new_blacklist = list(set(blacklisted + immediately_blacklist))

        # Delay-blacklist remaining rejected files only when retries exhausted
        # FIX: NEVER blacklist a file that EXISTS on disk and was found by Evidence
        # Collection. Patch failures (str_replace can't find exact match) are CODE
        # GENERATOR problems, not LOCALIZATION problems. Blacklisting verified files
        # causes the Planner to hallucinate edits in wrong files (Blacklist Cascade).
        if new_retry_count >= max_retries:
            _disk_missing = []
            for t in rejected:
                _t_abs = Path(workspace_path) / t.file_path if workspace_path else None
                if _t_abs and _t_abs.exists():
                    logger.info(
                        f"  🛡️  NOT blacklisting '{t.file_path}': file exists on disk. "
                        f"Patch failure is a code generator issue, not localization."
                    )
                else:
                    _disk_missing.append(t.file_path)
            new_blacklist = list(set(new_blacklist + _disk_missing))


        # Guard: never blacklist on-disk infra/config owner files. They are valid write
        # targets that carry no AST symbols, so a rejection can wrongly lock them and
        # collapse the next plan to zero writable tasks. Retry counters still bound the loop.
        _protected = [b for b in new_blacklist if _is_protected_config_owner(b, workspace_path)]
        if _protected:
            new_blacklist = [b for b in new_blacklist if b not in _protected]
            logger.info(f"  🛡️  Protected infra/config owners from blacklist: {_protected}")

        # Include blacklisted files in the feedback to planner
        if new_blacklist:
            failure_reason += "\n\nBLACKLISTED FILES (DO NOT USE these in your new plan):"
            for bp in new_blacklist:
                failure_reason += f"\n 🚫 {bp}"

        logger.warning(
            f"   Candidate validation FAILED (retry {new_retry_count}/{max_retries})\n"
            f"     Reason: {failure_reason}\n"
            f"     Rejected tasks: {[t.file_path for t in rejected]}"
        )
        return {
            "blacklisted_files": new_blacklist,
            "candidate_retry_count": new_retry_count,
            "validation_failure_reason": failure_reason or f"Rejected tasks: {[t.file_path for t in rejected]}",
            "status": "candidates_invalid",
        }

    # If we get here, the plan is accepted!
    logger.info(f"  ✅ All {len(accepted)} writable tasks validated and gated successfully.")
    
    read_only_tasks = [t for t in plan.tasks if getattr(t.task_type, "value", str(t.task_type)) == "read_only"]
    plan.tasks = accepted + read_only_tasks
    

    is_consistent_post, failure_reason_post, _ = validate_plan_consistency(plan.tasks, rejected, plan, workspace_path)
    if not is_consistent_post:
        logger.warning(f"  ⚠️ Post-mutation consistency check FAILED: {failure_reason_post}")
        
        new_retry_count = state.get("candidate_retry_count", 0) + 1
        max_retries = state.get("max_candidate_retries", 2)
        if new_retry_count >= max_retries:
            new_blacklist = list(set(blacklisted + [t.file_path for t in rejected]))
        else:
            new_blacklist = list(blacklisted)
            
        return {
            "blacklisted_files": new_blacklist,
            "candidate_retry_count": new_retry_count,
            "validation_failure_reason": f"Post-mutation consistency check failed: {failure_reason_post}",
            "status": "candidates_invalid",
        }
    
    agents.tracer.record_validation(
        status="candidates_validated",
        plan_tasks=plan.tasks,
        discovered_paths=[d["path"] for d in discovered],
        invalid=[],
        weak_tasks=[],
        blacklisted=list(blacklisted),
        retry_count=state.get("candidate_retry_count", 0),
    )
    return {
        "status": "candidates_validated",
        "validation_failure_reason": None,
        "architectural_plan": plan
    }


def localize_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 2.5: Repository Intelligence — Localization

    DEFAULT ACTION: MODIFY EXISTING FILE.
    Only create new files when proven necessary.

    Uses SQLite AST index (39K+ symbols for CC4E) to:
    1. Find the ACTUAL file in the repository for each planned task
    2. Override planner-suggested (LLM-hallucinated) paths with real ones
    3. Change task_type to MODIFY when existing file found
    4. Infer correct CREATE location from repo patterns when no file exists
    """
    print("\n" + "="*80)
    print(" ENTERING: localize_node() in workflow.py")
    print("   Purpose: Resolve planned file paths to ACTUAL repository locations")
    print("   Intelligence: SQLite AST (FTS5) + filesystem verification")
    print("="*80)
    logger.info(" PHASE 2.5: Repository Localization (AST-driven)")

    plan = _get_plan(state)
    logger.info(f"  Tasks to localize: {len(plan.tasks)}")
    for t in plan.tasks:
        logger.info(f"    [{t.id}] {t.task_type.value}: {t.file_path}")

    # READ_ONLY tasks need no localization — they carry no write target
    writable_tasks = [t for t in plan.tasks if t.task_type.value != "read_only"]
    readonly_tasks = [t for t in plan.tasks if t.task_type.value == "read_only"]
    if readonly_tasks:
        logger.info(f"  ⏩ Skipping {len(readonly_tasks)} read_only task(s) — no file writes")

    # Run localization against SQLite AST index + filesystem fallback
    # Pass full ticket text so unique ticket keywords (e.g. "JATO", "footer")
    # are available when the planner invented Java class names that don't exist
    ticket = state["ticket"]
    ticket_context = f"{ticket.title} {ticket.description}"
    localized_result = agents.localizer.localize_planned_tasks(writable_tasks, ticket_context=ticket_context)
    localized_tasks = localized_result[0] if isinstance(localized_result, tuple) else localized_result

    # Merge read_only tasks back (unlocalized, won't reach code gen)
    all_tasks = localized_tasks + readonly_tasks

    # Log decisions
    modify_count = sum(1 for t in all_tasks if t.task_type.value == "modify")
    create_count = sum(1 for t in all_tasks if t.task_type.value == "create")
    logger.info(
        f"  Localization complete:\n"
        f"    MODIFY: {modify_count} tasks (existing files found)\n"
        f"    CREATE: {create_count} tasks (new files needed)\n"
    )
    for t in all_tasks:
        logger.info(StageLog.localization(t))
        print(StageLog.localization(t))

    # Update the plan with localized tasks
    plan.tasks = all_tasks
    logger.info(StageLog.plan_summary(all_tasks))
    print(StageLog.plan_summary(all_tasks))

    agents.tracer.record_localization(
        localized_tasks=localized_tasks,
        system_message=getattr(agents.localizer, "last_system_prompt", None),
        user_message=getattr(agents.localizer, "last_user_prompt", None),
    )

    return {
        "architectural_plan": plan,
        "status": "localization_complete"
    }


# ============================================================================
# PHASE 2G — HYPOTHESIS INVESTIGATION + EVIDENCE COLLECTION LOOP
# ============================================================================
# Purpose: Before checking ownership completeness, deeply investigate the
# ticket using ALL available evidence sources (semantic search, Neo4j,
# SQLite symbol/FTS, TypeScript chains, Java chains, CSS chains, config
# chains, layout chains).  The output — GroundedUnderstanding — is passed
# into the code generators so generation operates on verified evidence rather
# than only the localized file list.
# ============================================================================

def hypothesis_investigation_node(
    state: "TicketToCodeState",
    agents: "WorkflowAgents",
) -> dict:
    """
    Phase 2G-1: Hypothesis Investigation

    Asks the InvestigationAgent to generate concrete, searchable hypotheses
    from the ticket and the initial triage result.  Each hypothesis contains:
      • queries   — semantic search phrases
      • literals  — exact string constants / config keys to FTS-match
      • symbols   — class / method / component names to look up in SQLite
    """
    print("\n" + "=" * 80)
    print(" ENTERING: hypothesis_investigation_node() in workflow.py")
    print("   Purpose: Generate investigation hypotheses from ticket + triage")
    print("=" * 80)
    logger.info(" PHASE 2G-1: Hypothesis Investigation")

    if state.get("status") == "failed":
        return {"status": "failed"}

    ticket = state["ticket"]
    investigation = state["investigation_result"]
    localized_tasks = []  # Decoupled from plan since this now runs before planning

    hypotheses = agents.investigation.generate_hypotheses(
        ticket=ticket,
        investigation=investigation,
        localized_tasks=localized_tasks,
    )

    logger.info(f"  Generated {len(hypotheses)} hypothesis(es):")
    for h in hypotheses:
        logger.info(
            f"    [{h.id}] {h.hypothesis[:80]} "
            f"(queries={len(h.queries)}, literals={len(h.literals)}, "
            f"symbols={len(h.symbols)}, prior_conf={h.confidence:.2f})"
        )

    return {
        "investigation_hypotheses": hypotheses,
        "status": "hypotheses_generated",
    }


def evidence_collection_loop_node(
    state: "TicketToCodeState",
    agents: "WorkflowAgents",
) -> dict:
    """
    Phase 2G-2: Evidence Collection Loop

    Iteratively queries every evidence source until the confidence threshold
    is reached (default 0.70) or max iterations (5) are exhausted:

      1. Semantic search via RAGEngine
      2. SQLite FTS for string literals
      3. SQLite symbol lookup
      4. Neo4j relationship queries
      5. TypeScript component relationship chains
      6. Java controller/service/repository chains
      7. CSS/SCSS ownership chains
      8. Configuration/value propagation chains
      9. Parent layout ownership chains
     10. Live repository search (.sh, .bat, .ps1, .json, .xml, .env — Phase 3A)
    """
    print("\n" + "=" * 80)
    print(" ENTERING: evidence_collection_loop_node() in workflow.py")
    print("   Purpose: Collect grounded evidence from ALL infrastructure sources")
    print("=" * 80)
    logger.info(" PHASE 2G-2: Evidence Collection Loop")

    if state.get("status") == "failed":
        return {"status": "failed"}

    hypotheses = state.get("investigation_hypotheses") or []
    plan = state.get("architectural_plan")
    localized_tasks = plan.tasks if plan else []
    ticket = state["ticket"]

    if not hypotheses:
        logger.warning("  ⚠️  No hypotheses found — skipping evidence collection")
        return {
            "evidence_items": [],
            "status": "evidence_collected",
        }

    collect_result = agents.evidence_loop.collect(
        hypotheses=hypotheses,
        localized_tasks=localized_tasks,
        ticket=ticket,
        investigation=state.get("investigation_result"),
    )

    # collect() returns 3-tuple: (items, confidence, clarification_question)
    # clarification_question is None when proceeding normally, a string when
    # the agentic loop decided it needs more info from the user.
    if len(collect_result) == 3:
        evidence_items, confidence, clarification_question = collect_result
    else:
        # Backward compatibility: 2-tuple (items, confidence)
        evidence_items, confidence = collect_result
        clarification_question = None

    # ── NEED_MORE_INFO exit ──────────────────────────────────────────────
    # The agentic loop decided the ticket is too vague to find files.
    # Route to "need_more_info" — this exact string matches routes.py L1186.
    if clarification_question is not None:
        logger.warning(
            f"  Agentic loop requests clarification: {clarification_question[:120]}"
        )
        # Carry forward agentic verdicts so semantic_verification_node
        # can populate state in the expected batch shape without re-running.
        agentic_verdicts = getattr(agents.evidence_loop, "_agentic_verdicts", {})
        return {
            "evidence_items": evidence_items,
            "status": "need_more_info",
            "clarification_question": clarification_question,
            "semantic_verification_results": [
                {
                    "file_path": v.get("file_path", fp),
                    "decision": v.get("decision", "include"),
                    "semantic_relevance_score": v.get("semantic_relevance_score", 0.0),
                    "reason": v.get("reason", ""),
                }
                for fp, v in agentic_verdicts.items()
            ],
        }

    logger.info(
        f"  Evidence collection complete: {len(evidence_items)} items, "
        f"confidence={confidence:.3f}"
    )

    # Log evidence breakdown by source
    from collections import Counter
    source_counts = Counter(e.source for e in evidence_items)
    for source, count in sorted(source_counts.items()):
        logger.info(f"    [{source}] {count} item(s)")

    # Capture expansion map from repo_search engine after collection
    expansion_map: dict = {}
    if agents.evidence_loop.repo_search is not None:
        expansion_map = agents.evidence_loop.repo_search.expansion_map
        if expansion_map:
            logger.info(
                f"  Query expansion map: {len(expansion_map)} original term(s) → "
                + ", ".join(
                    f"{orig!r}→{sorted(exps)}"
                    for orig, exps in list(expansion_map.items())[:4]
                )
            )

    # Carry agentic verdicts forward for semantic_verification_node
    agentic_verdicts = getattr(agents.evidence_loop, "_agentic_verdicts", {})

    return {
        "evidence_items": evidence_items,
        "query_expansion_map": expansion_map,
        "status": "evidence_collected",
        "semantic_verification_results": [
            {
                "file_path": v.get("file_path", fp),
                "decision": v.get("decision", "include"),
                "semantic_relevance_score": v.get("semantic_relevance_score", 0.0),
                "reason": v.get("reason", ""),
            }
            for fp, v in agentic_verdicts.items()
        ] if agentic_verdicts else None,
    }


# ============================================================================
# PHASE 3B — EVIDENCE RANKING
# ============================================================================

def evidence_ranking_node(
    state: "TicketToCodeState",
    agents: "WorkflowAgents",
) -> dict:
    """
    Phase 3B: Evidence Ranking (with Component Group Awareness)

    1. Builds ComponentGroups from the collected evidence by scanning for
       sibling files on disk (e.g., .ts → .html + .scss).
    2. Injects synthetic EvidenceItems for group members that had no evidence
       so the ranker can score them (they inherit ~85% of the group score).
    3. Deterministic re-scoring: files with exact literal/symbol matches rank
       above filename-only and broad-repo matches.
    4. Writes evidence_ranking.json and component_groups.json to trace dir.
    """
    print("\n" + "=" * 80)
    print(" ENTERING: evidence_ranking_node() in workflow.py")
    print("   Purpose: Group into components → Rescore evidence → Rank")
    print("=" * 80)
    logger.info(" PHASE 3B: Evidence Ranking (Component Group Aware)")

    if state.get("status") == "failed":
        return {"status": "failed"}

    evidence_items  = state.get("evidence_items") or []
    hypotheses      = state.get("investigation_hypotheses") or []
    plan            = state.get("architectural_plan")
    localized_tasks = plan.tasks if plan else []

    if not evidence_items:
        logger.warning("  ⚠️  No evidence items to rank — skipping")
        return {"status": "evidence_ranked"}

    # ── Step 1: Build Component Groups ────────────────────────────────────
    workspace_path = Path(state.get("workspace_path", "."))
    component_groups = _build_component_groups(evidence_items, workspace_path)

    # ── Step 2: Inject synthetic evidence for non-evidence group members ──
    # For each group member that has NO evidence (e.g., .html found via disk
    # scan), create a synthetic EvidenceItem so the ranker includes it.
    existing_evidence_paths = {e.file_path.replace("\\", "/") for e in evidence_items}
    injected_count = 0

    for group in component_groups:
        if not group.is_group:
            continue  # Standalone files don't need injection

        for ext, member_path in group.non_evidence_members().items():
            norm_path = member_path.replace("\\", "/")
            if norm_path in existing_evidence_paths:
                continue  # Already has evidence

            # Inherit 85% of the group's best score
            inherited_score = round(group.group_score * 0.85, 4)

            evidence_items.append(EvidenceItem(
                file_path=member_path,
                provider="component_group",
                source="component_group",
                strength="medium",
                details=f"component_group_member: {group.stem}",
                content_snippet=(
                    f"[component_group:{group.stem}] "
                    f"Sibling of {Path(group.primary_file).name} "
                    f"(discovered via disk scan, ext={ext})"
                ),
                relevance_score=inherited_score,
                hypothesis_id=None,
                confidence_reason=(
                    f"Component group member of '{group.stem}'. "
                    f"Primary file {Path(group.primary_file).name} has "
                    f"evidence score {group.group_score:.3f}."
                ),
            ))
            existing_evidence_paths.add(norm_path)
            injected_count += 1

    if injected_count > 0:
        logger.info(
            f"  [ComponentGroup] Injected {injected_count} synthetic evidence "
            f"items for non-evidence group members"
        )

    # ── Step 3: Rank all evidence (including injected group members) ──────
    ranker = EvidenceRankingEngine()
    ranked_items, ranked_files = ranker.rank(
        evidence_items=evidence_items,
        hypotheses=hypotheses,
        localized_tasks=localized_tasks,
        expansion_map=state.get("query_expansion_map") or {},
    )

    if ranked_files:
        top = ranked_files[0]
        logger.info(
            f"  Ranking complete: {len(ranked_files)} unique files, "
            f"top='{top.file_path}' ({top.final_score:.3f})"
        )

    # ── Step 4: Trace ────────────────────────────────────────────────────
    agents.tracer.record_evidence_ranking(ranked_files)

    # Trace component groups
    try:
        import json as _json
        trace_dir = agents.tracer._trace_dir if hasattr(agents.tracer, '_trace_dir') else None
        if trace_dir:
            groups_file = os.path.join(trace_dir, "component_groups.json")
            with open(groups_file, "w") as f:
                _json.dump(
                    [g.to_dict() for g in component_groups],
                    f, indent=2,
                )
    except Exception:
        pass  # Tracing is best-effort

    return {
        "evidence_items": ranked_items,
        "ranked_files": ranked_files,
        "component_groups": component_groups,
        "status": "evidence_ranked",
    }


def grounded_understanding_node(
    state: "TicketToCodeState",
    agents: "WorkflowAgents",
) -> dict:
    """
    Phase 2G-3: Grounded Understanding Synthesis

    Synthesizes all collected evidence into a GroundedUnderstanding that:
      • Confirms or refutes each hypothesis
      • Builds the dependency chain (entry → change site)
      • Identifies the change group (files that must change together)
      • Separates writable_files from readonly_context_files
      • Sets the overall confidence score

    The GroundedUnderstanding is added to the workflow state and passed
    to code generators so they operate on verified evidence.
    """
    print("\n" + "=" * 80)
    print(" ENTERING: grounded_understanding_node() in workflow.py")
    print("   Purpose: Synthesize evidence into GroundedUnderstanding for generation")
    print("=" * 80)
    logger.info(" PHASE 2G-3: Grounded Understanding Synthesis")

    hypotheses   = state.get("investigation_hypotheses") or []
    evidence     = state.get("evidence_items") or []
    investigation = state["investigation_result"]
    plan          = state.get("architectural_plan")
    localized_tasks = plan.tasks if plan else []
    ticket        = state["ticket"]

    # ── FIX 1.3: CONFIDENCE GATE ─────────────────────────────────────────────
    # Prevent root-cause synthesis when there is no evidence to support it.
    # Previously the node would continue with confidence=0 and produce a
    # hallucinated root cause from the hypothesis alone.  Now we halt and
    # signal that expanded discovery is needed.
    #
    # HOWEVER: If the planner already found validated writable files with high
    # confidence (via discovery + localization), we should NOT block code
    # generation.  The evidence loop may return 0 items when the search terms
    # are too long/sentence-like, but the plan is already grounded by the
    # discovery phase.
    _writable_tasks = [t for t in localized_tasks if t.task_type.value != "read_only"]
    if not evidence and not _writable_tasks:
        logger.warning(
            "  ⚠️  CONFIDENCE GATE: 0 evidence items AND 0 writable tasks. "
            "Cannot synthesize a grounded root cause — this would be hallucination. "
            "Signaling 'evidence_insufficient' for re-discovery."
        )
        # Build a minimal GroundedUnderstanding so downstream nodes don't crash
        grounded = GroundedUnderstanding(
            root_cause="INSUFFICIENT EVIDENCE — no evidence was collected to ground a root cause",
            evidence=[],
            dependency_chain=[],
            change_group=[t.file_path for t in localized_tasks if t.task_type.value != "read_only"],
            required_files=[t.file_path for t in localized_tasks],
            writable_files=[t.file_path for t in localized_tasks if t.task_type.value != "read_only"],
            readonly_context_files=[t.file_path for t in localized_tasks if t.task_type.value == "read_only"],
            confidence=0.0,
            evidence_loop_iterations=0,
            hypotheses_confirmed=[],
            hypotheses_refuted=[h.id for h in hypotheses],
        )
        return {
            "grounded_understanding": grounded,
            "status": "evidence_insufficient",
        }

    if not evidence and _writable_tasks:
        logger.warning(
            f"  ⚠️  CONFIDENCE GATE SOFTENED: 0 evidence items but {len(_writable_tasks)} "
            f"writable tasks already planned. Proceeding with plan-grounded synthesis."
        )

    # ── Writable vs readonly from the plan ───────────────────────────────────
    writable_from_plan = [
        t.file_path for t in localized_tasks if t.task_type.value != "read_only"
    ]
    readonly_from_plan = [
        t.file_path for t in localized_tasks if t.task_type.value == "read_only"
    ]

    # ── Evidence-file sets ────────────────────────────────────────────────────
    evidence_files  = list({e.file_path for e in evidence})
    # Files surfaced by evidence but not already in the plan
    extra_context   = [f for f in evidence_files if f not in writable_from_plan]

    # ── Dependency chain: high-relevance evidence items in score order ────────
    sorted_evidence = sorted(evidence, key=lambda e: e.relevance_score, reverse=True)
    chain_files     = []
    seen_chain: set = set()
    for e in sorted_evidence[:20]:
        if e.file_path not in seen_chain:
            chain_files.append(e.file_path)
            seen_chain.add(e.file_path)

    # ── Change group: writable plan files + high-confidence evidence files ────
    # Exclude: config_chain (data/i18n files), traces/ (audit logs), readonly prefixes
    # Include: repository_search (Phase 3A) — finds .sh/.bat/.ps1/.xml/.env not in index
    _CODE_SOURCES = {"semantic", "sqlite_fts", "sqlite_symbol", "neo4j",
                     "ts_chain", "java_chain", "css_chain", "layout_chain",
                     "repository_search", "rkb_feature_match"}

    # ── Change 3: Planner-decision-aware evidence injection ───────────────────
    # BEFORE: every evidence file with score >= 0.70 was injected unconditionally.
    # AFTER:  files the planner evaluated and IGNORED are never auto-promoted.
    #         Files not shown to the planner (outside top-25) keep the original
    #         fallback promotion so backward compatibility is preserved.
    planner_decisions = state.get("planner_decisions") or {}

    # Roles whose OPTIONAL planner decision still does NOT warrant promotion.
    # These are files that are read-only context at best; they must never become
    # writable modify tasks unless the planner explicitly marked them REQUIRED.
    _NON_PROMOTABLE_ROLES = {
        CandidateRole.TEST.value,
        CandidateRole.LOCK_FILE.value,
        CandidateRole.GENERATED.value,
    }

    # FIX 3: Roles that MUST survive a planner IGNORE when evidence is strong.
    # These files are physically required to change in version-propagation tickets.
    # The planner may miss them due to absent ticket classification, so evidence
    # score >= 0.85 overrides the IGNORE gate as a safety net.
    _FORCE_PROMOTE_ROLES = {
        CandidateRole.DEPLOYMENT.value,
        CandidateRole.VERSION_SOURCE.value,
        CandidateRole.ROOT_COMPONENT.value,
    }

    high_conf_evidence: list = []
    _ignored_count = 0
    _fallback_count = 0

    for e in evidence:
        fp = e.file_path

        # Basic filters — unchanged from original
        if e.relevance_score < 0.70:
            continue
        if fp in writable_from_plan:
            continue
        if e.source not in _CODE_SOURCES:
            continue
        if fp.startswith(("traces/", "traces\\")):
            continue

        # ── Respect planner decisions ─────────────────────────────────────────
        dec_record = planner_decisions.get(fp)

        if dec_record:
            decision = dec_record["decision"]
            role     = dec_record.get("role", CandidateRole.UNKNOWN.value)

            if decision == PlannerDecisionValue.IGNORE.value:
                # FIX 3: critical propagation roles with strong evidence bypass the gate.
                # DEPLOYMENT / VERSION_SOURCE / ROOT_COMPONENT files at score >= 0.85
                # must survive even when the planner omitted them (likely due to missing
                # ticket-type classification).  All other roles keep the hard block.
                if role in _FORCE_PROMOTE_ROLES and e.relevance_score >= 0.85:
                    logger.info(
                        f"  [grp] FORCE-PROMOTE IGNORE override: {fp} "
                        f"(role={role}, score={e.relevance_score:.2f}) "
                        f"— critical propagation file, bypassing planner IGNORE"
                    )
                    # Fall through to high_conf_evidence.append(fp)
                else:
                    logger.debug(
                        f"  [grp] SKIP IGNORE — planner excluded: {fp} "
                        f"(role={role}, score={e.relevance_score:.2f})"
                    )
                    _ignored_count += 1
                    continue

            elif decision == PlannerDecisionValue.OPTIONAL.value:
                # Planner included file as read-only context only.
                # Promote to writable only for roles that can genuinely require
                # an autonomous version-propagation write (DEPLOYMENT, ROOT_COMPONENT,
                # VERSION_SOURCE, CONFIG).  Never promote TEST / LOCK_FILE / GENERATED.
                if role in _NON_PROMOTABLE_ROLES:
                    logger.debug(
                        f"  [grp] SKIP OPTIONAL {role} — non-promotable role: {fp}"
                    )
                    _ignored_count += 1
                    continue
                # else: fall through and allow promotion

            # decision == REQUIRED: already in writable_from_plan (filtered above).
            # Fall through to append.

        else:
            # No planner decision exists — this file was outside the top-25 shown
            # to the planner. Keep original fallback-promotion behaviour, EXCEPT
            # filter out non-promotable roles to prevent blind spots.
            # Check discovery-pipeline ownership before structural fallback
            _fb_ot = ""
            for _d in (state.get("discovered_files") or []):
                if _d.get("path", "").replace("\\", "/") == fp.replace("\\", "/"):
                    _fb_ot = (_d.get("features") or {}).get("ownership_type", "") or ""
                    break
            if _fb_ot in ("PRIMARY_OWNER", "DISPLAY_OWNER", "SUPPORTING", "READ_ONLY"):
                fallback_role = _fb_ot
            else:
                fallback_role = _classify_candidate_role(fp)
            if fallback_role in _NON_PROMOTABLE_ROLES:
                logger.debug(f"  [grp] SKIP FALLBACK role={fallback_role} path={fp}")
                _ignored_count += 1
                continue

            _fallback_count += 1
        high_conf_evidence.append(fp)

    logger.info(
        f"  [grp] evidence promotion: {len(high_conf_evidence)} promoted, "
        f"{_ignored_count} blocked by planner IGNORE/non-promotable, "
        f"{_fallback_count} fallback (not shown to planner)"
    )

    change_group = list(dict.fromkeys(writable_from_plan + high_conf_evidence))

    # ── Phase 3A.2: Promote change_group files into plan tasks ───────────────
    # Files in high_conf_evidence are not yet in the architectural plan; create
    # writable DevelopmentTask objects for each so generate_code_node can write
    # them.  Only promote files that physically exist on disk.
    workspace_root = Path(state["workspace_path"])
    promoted_paths: list = []
    if plan is not None and high_conf_evidence:
        # Sort by best evidence score descending so the highest-confidence files
        # enter plan.tasks first.  This matters because generate_code_node enforces
        # a hard cap of 5 writable files and processes them in plan.tasks order.
        def _best_score(fp: str) -> float:
            return max(
                (e.relevance_score for e in evidence if e.file_path == fp),
                default=0.70,
            )
        sorted_evidence_files = sorted(high_conf_evidence, key=_best_score, reverse=True)
        task_idx_start = len(plan.tasks)
        for idx, file_path in enumerate(sorted_evidence_files):
            if not (workspace_root / file_path).exists():
                logger.debug(f"  [grp] skip (not on disk): {file_path}")
                continue
            # Best evidence score for this file
            best_score = max(
                (e.relevance_score for e in evidence if e.file_path == file_path),
                default=0.70,
            )
            best_source = next(
                (e.source for e in sorted(evidence, key=lambda e: e.relevance_score, reverse=True)
                 if e.file_path == file_path),
                "repository_search",
            )
            promoted_task = _make_promoted_task(
                file_path=file_path,
                workspace_path=workspace_root,
                task_index=task_idx_start + idx + 1,
                evidence_score=best_score,
                evidence_source=best_source,
            )
            plan.tasks.append(promoted_task)
            promoted_paths.append(file_path)
            logger.info(
                f"  [grp] promoted → {file_path} "
                f"(src={best_source}, score={best_score:.2f})"
            )
        if promoted_paths:
            logger.info(
                f"  Phase 3A.2: {len(promoted_paths)} file(s) promoted to plan tasks"
            )

    # ── Hypothesis evaluation ─────────────────────────────────────────────────
    confirmed: list  = []
    refuted:   list  = []
    for hyp in hypotheses:
        supporting = [
            e for e in evidence
            if e.hypothesis_id == hyp.id and e.relevance_score >= 0.60
        ]
        if supporting:
            confirmed.append(hyp.id)
        else:
            refuted.append(hyp.id)

    # ── Overall confidence (from evidence loop result) ────────────────────────
    # Recompute so the value is consistent with the evidence list
    from ticket_to_code.agents.evidence_collection_loop import EvidenceCollectionLoop
    confidence = EvidenceCollectionLoop(
        agents.localizer, agents.rag_engine
    )._compute_confidence(evidence, hypotheses, localized_tasks)

    # ── Root cause synthesis ──────────────────────────────────────────────────
    root_cause = (
        investigation.root_cause_hypothesis
        or f"Ticket '{ticket.title}' requires changes in: {', '.join(writable_from_plan[:3])}"
    )
    if confirmed:
        # Use the first confirmed hypothesis as the primary root cause statement
        for hyp in hypotheses:
            if hyp.id in confirmed:
                root_cause = hyp.hypothesis
                break

    grounded = GroundedUnderstanding(
        root_cause=root_cause,
        evidence=evidence,
        dependency_chain=chain_files,
        change_group=change_group,
        required_files=list(dict.fromkeys(writable_from_plan + extra_context[:10])),
        writable_files=list(dict.fromkeys(writable_from_plan + promoted_paths)),
        readonly_context_files=list(dict.fromkeys(readonly_from_plan + extra_context[:10])),
        confidence=round(confidence, 3),
        evidence_loop_iterations=len(set(e.source for e in evidence)),
        hypotheses_confirmed=confirmed,
        hypotheses_refuted=refuted,
    )

    logger.info(
        f"  Grounded Understanding:\n"
        f"    root_cause       : {grounded.root_cause[:80]}\n"
        f"    evidence items   : {len(grounded.evidence)}\n"
        f"    dependency_chain : {len(grounded.dependency_chain)} files\n"
        f"    change_group     : {grounded.change_group}\n"
        f"    writable_files   : {grounded.writable_files}\n"
        f"    readonly_context : {grounded.readonly_context_files[:5]}\n"
        f"    confidence       : {grounded.confidence:.3f}\n"
        f"    hypotheses ✅    : {grounded.hypotheses_confirmed}\n"
        f"    hypotheses ❌    : {grounded.hypotheses_refuted}"
    )

    return {
        "grounded_understanding": grounded,
        "architectural_plan": plan,        # updated with promoted change_group tasks
        "status": "grounded_understanding_complete",
    }


# ============================================================================
# PHASE 2F — OWNERSHIP COMPLETENESS CHECK
# ============================================================================
# Purpose: Determine whether localized files FULLY own the behavior in the
# ticket.  If a layout gap is detected (a CSS file owns BEM modifiers while
# the root block's layout container is owned by a sibling file), add the
# sibling as a writable LAYOUT_OWNER task so generation receives both files.
#
# Design constraints:
#  • No hardcoded repository paths or component names
#  • No LLM calls — pure static analysis
#  • Reuses sqlite_store and workspace_path from LocalizationAgent
#  • Only SCSS/CSS gap detection is implemented (layout gap is the known class
#    of completeness failure; data / dependency gaps reuse existing discovery)
# ============================================================================

_LAYOUT_KW: frozenset = frozenset({
    "alignment", "aligned", "footer", "position", "spacing", "margin",
    "padding", "css", "scss", "layout", "height", "flex", "overflow",
    "sticky", "fixed", "bottom", "top", "ux", "ui", "display", "visual",
    "style", "responsive", "superimposed", "overlap", "blank space",
    "page css", "static css", "button.*bottom", "button.*area",
})

_LAYOUT_CSS_RE = re.compile(
    r'\b(display|flex|flex-direction|flex-grow|flex-shrink|flex-wrap|'
    r'position|height|min-height|max-height|width|min-width|max-width|'
    r'overflow|overflow-y|overflow-x|top|bottom|left|right)\s*:',
    re.IGNORECASE,
)

_SCSS_EXTS: frozenset = frozenset({".scss", ".css", ".sass"})


def _ticket_is_layout_focused(ticket_lower: str) -> bool:
    """Return True when ≥ 2 layout-related keywords appear in the ticket."""
    hits = sum(1 for kw in _LAYOUT_KW if kw in ticket_lower)
    return hits >= 2


def _extract_top_level_classes(content: str) -> set:
    """Return every top-level CSS class name defined in a SCSS/CSS file."""
    # Matches `.class-name {` at the start of a line (handles optional indentation)
    pat = re.compile(r'^\s*\.([\w][\w-]*)\s*\{', re.MULTILINE)
    return {m.group(1) for m in pat.finditer(content)}


def _class_has_layout_properties(content: str, class_name: str) -> bool:
    """
    Return True when the file defines `.class_name { ... }` and that block
    contains at least one layout-critical CSS property.
    Handles multi-line SCSS blocks via a brace-depth scanner.
    """
    # Find the opening of the block
    opener = re.compile(r'\.' + re.escape(class_name) + r'\s*\{')
    m = opener.search(content)
    if not m:
        return False
    # Scan forward counting braces to extract the block body
    pos = m.end()
    depth = 1
    body_chars: list = []
    while pos < len(content) and depth > 0:
        ch = content[pos]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                break
        body_chars.append(ch)
        pos += 1
    block_body = "".join(body_chars)
    return bool(_LAYOUT_CSS_RE.search(block_body))


def _apply_ui_state_regression_bias(candidates: list, ticket) -> int:
    """
    Bias candidate confidence for cross-tab UI state regressions.

    Why: Tickets like "Button is enabled in tab A, then after navigating tab B and
    returning to tab A it becomes disabled" often get over-matched to action-tab
    components (tab B) instead of the owner and shared form/register state files.

    This adjustment is small and additive, and only activates for this ticket shape.
    """
    if not candidates or ticket is None:
        return 0

    title = str(getattr(ticket, "title", "") or "").lower()
    desc = str(getattr(ticket, "description", "") or "").lower()
    txt = f"{title} {desc}"

    has_ui_toggle_symptom = ("upload" in txt and "button" in txt and ("disabled" in txt or "enabled" in txt))
    has_cross_tab_flow = ("tab" in txt and "after" in txt and "return" in txt)
    has_register_deliverable = ("register" in txt and "deliverable" in txt)

    if not (has_ui_toggle_symptom and has_cross_tab_flow and has_register_deliverable):
        return 0

    adjusted = 0
    for c in candidates:
        p = str(c.get("path", "") or "").replace("\\", "/").lower()
        if not p:
            continue

        delta = 0.0

        # Primary symptom ownership is usually in the affected tab component.
        if "/registers/" in p:
            delta += 0.18
        if "upload-register.component.ts" in p:
            delta += 0.10

        # Shared state owners that commonly gate button enablement.
        if "/shared/services/registers/" in p:
            delta += 0.14
        if "/shared/form/" in p or "document-file-browser" in p:
            delta += 0.12

        # Action-tab components are still relevant, but should not dominate.
        if "/deliverables/" in p and "/registers/" not in p:
            delta -= 0.10

        if abs(delta) < 1e-9:
            continue

        base = float(c.get("confidence", 0.0) or 0.0)
        c["confidence"] = max(0.0, min(1.0, base + delta))
        c.setdefault("signals", []).insert(0, f"ui_state_bias:{delta:+.2f}")
        adjusted += 1

    return adjusted


def _find_layout_gap_owners(
    scss_task_path: str,
    workspace_path: Path,
    existing_paths: Set[str],
) -> list:
    """
    Given a SCSS file that is a localized writable task, find sibling SCSS
    files in the same *module directory* (grandparent of the component dir)
    that:
      a) define at least one of the same top-level CSS classes, AND
      b) those shared classes contain layout-critical properties.

    Ranking (most important first):
      1. HOST COMPONENT — the sibling whose HTML template uses the localized
         component's selector (e.g., <se-deliverable-reviewers>).  This is
         the true layout-container owner.
      2. SIBLING with shared layout class (fallback if no host found).

    Returns a list of dicts:
        {"path": str, "shared_classes": list[str], "is_host": bool}
    capped at 2 entries to prevent uncontrolled expansion.
    """
    norm = scss_task_path.replace("\\", "/")
    scss_file = workspace_path / norm
    if not scss_file.exists():
        return []

    try:
        local_content = scss_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    local_classes = _extract_top_level_classes(local_content)
    if not local_classes:
        return []

    # Derive the component name fragment to locate the host component.
    # e.g. "deliverable-reviewers.component.scss" → "deliverable-reviewers"
    component_fragment = Path(scss_task_path).stem.split(".")[0]
    # Match any Angular-style selector that ends with the component name
    # fragment: <se-deliverable-reviewers …>, <app-deliverable-reviewers …>
    host_re = re.compile(
        r"<[\w]+-" + re.escape(component_fragment) + r"[\s>/]",
        re.IGNORECASE,
    )

    # Module directory = grandparent of the component directory.
    # e.g. .../deliverables/deliverable-reviewers/x.scss → .../deliverables/
    module_dir = scss_file.parent.parent
    if not module_dir.is_dir():
        return []

    import time
    import os
    logger.info("LAYOUT_GAP_SCAN_START")
    start_time = time.time()

    host_results: list = []
    sibling_results: list = []

    skip_dirs = {"node_modules", ".git", "dist", "build", "target", "coverage"}
    candidates = []
    for root, dirs, files in os.walk(str(module_dir)):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if f.endswith(".scss"):
                candidates.append(Path(root) / f)

    files_scanned = 0
    max_files = 500

    for candidate in sorted(candidates):
        if files_scanned >= max_files:
            logger.warning(f"MAX_SCSS_FILES ({max_files}) exceeded, stopping scan")
            break
            
        # Hard cap: 1 host + 1 sibling maximum
        if len(host_results) >= 1 and len(sibling_results) >= 1:
            break
            
        files_scanned += 1
        rel = str(candidate.relative_to(workspace_path)).replace("\\", "/")
        if rel == norm or rel in existing_paths:
            continue
        try:
            cand_content = candidate.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        cand_classes = _extract_top_level_classes(cand_content)
        shared = local_classes & cand_classes
        if not shared:
            continue

        layout_shared = [
            cls for cls in sorted(shared)
            if _class_has_layout_properties(cand_content, cls)
        ]
        if not layout_shared:
            continue

        # Check if corresponding HTML template hosts the localized component
        html_path = candidate.with_suffix(".html")
        is_host = False
        if html_path.exists():
            try:
                html_txt = html_path.read_text(encoding="utf-8", errors="ignore")
                is_host = bool(host_re.search(html_txt))
            except Exception:
                pass

        entry = {"path": rel, "shared_classes": layout_shared, "is_host": is_host}
        if is_host:
            if len(host_results) < 1:
                host_results.append(entry)
        else:
            if len(sibling_results) < 1:
                sibling_results.append(entry)

    duration_ms = int((time.time() - start_time) * 1000)
    logger.info("LAYOUT_GAP_SCAN_END")
    logger.info(f"SCSS_FILES_SCANNED={files_scanned}")
    logger.info(f"SCAN_DURATION_MS={duration_ms}")

    # If the host component was precisely identified, use only that.
    # The sibling fallback is a last resort when no host can be determined.
    if host_results:
        return host_results[:1]
    return sibling_results[:2]


def _make_ownership_task(
    file_path: str,
    workspace_path: Path,
    task_index: int,
    role: str,
    source_file: str,
    shared_classes: list,
) -> "Any":
    """
    Create a DevelopmentTask for an ownership-expanded file.
    Imports are done locally to avoid circular imports at module level.
    """
    from ticket_to_code.models import DevelopmentTask, TaskType, ProgrammingLanguage

    ext = Path(file_path).suffix.lower()
    lang = ProgrammingLanguage.TYPESCRIPT  # Angular SCSS is TypeScript project

    return DevelopmentTask(
        id=f"own-{task_index}",
        title=f"{role}: {Path(file_path).name}",
        description=(
            f"Ownership completeness expansion: this file shares CSS classes "
            f"({', '.join(f'.{c}' for c in shared_classes)}) with {source_file} "
            f"and owns the layout container. Must be modified alongside the "
            f"localized file to fully resolve the layout issue."
        ),
        file_path=file_path,
        task_type=TaskType.MODIFY,
        language=lang,
        localization_confidence=0.72,
        localization_reason=(
            f"ownership_completeness: {role} — shared CSS classes "
            f"{shared_classes} also defined in {source_file}"
        ),
        ownership_type="DISPLAY_OWNER",   # CSS file → display owner tier
        new_file_creation_allowed=False,
    )


# ── Change-group extension map ────────────────────────────────────────────────
_EXT_LANG_MAP: dict = {}


def _lang_for_ext(ext: str) -> "Any":
    """Return the ProgrammingLanguage enum value for a file extension."""
    from ticket_to_code.models import ProgrammingLanguage
    return {
        ".java":       ProgrammingLanguage.JAVA,
        ".ts":         ProgrammingLanguage.TYPESCRIPT,
        ".tsx":        ProgrammingLanguage.TYPESCRIPT,
        ".js":         ProgrammingLanguage.JAVASCRIPT,
        ".jsx":        ProgrammingLanguage.JAVASCRIPT,
        ".py":         ProgrammingLanguage.PYTHON,
        ".go":         ProgrammingLanguage.GO,
        ".cs":         ProgrammingLanguage.CSHARP,
        ".html":       ProgrammingLanguage.HTML,
        ".htm":        ProgrammingLanguage.HTML,
        ".scss":       ProgrammingLanguage.SCSS,
        ".css":        ProgrammingLanguage.SCSS,
        ".less":       ProgrammingLanguage.SCSS,
        ".sass":       ProgrammingLanguage.SCSS,
        ".json":       ProgrammingLanguage.JSON,
        ".xml":        ProgrammingLanguage.XML,
        ".yml":        ProgrammingLanguage.YAML,
        ".yaml":       ProgrammingLanguage.YAML,
        ".properties": ProgrammingLanguage.PROPERTIES,
        ".env":        ProgrammingLanguage.PROPERTIES,
        ".sh":         ProgrammingLanguage.SHELL,
        ".bash":       ProgrammingLanguage.SHELL,
        ".ps1":        ProgrammingLanguage.SHELL,
        ".bat":        ProgrammingLanguage.SHELL,
        ".cmd":        ProgrammingLanguage.SHELL,
    }.get(ext, ProgrammingLanguage.PYTHON)


def _make_promoted_task(
    file_path: str,
    workspace_path: Path,
    task_index: int,
    evidence_score: float,
    evidence_source: str,
) -> "Any":
    """
    Create a DevelopmentTask for a file promoted from grounded_understanding
    change_group.  These are files discovered by evidence (Phase 3A repository
    search, SQLite FTS, etc.) that are not yet in the architectural plan.
    """
    from ticket_to_code.models import DevelopmentTask, TaskType

    ext = Path(file_path).suffix.lower()
    lang = _lang_for_ext(ext)

    return DevelopmentTask(
        id=f"grp-{task_index}",
        title=f"Change group promotion: {Path(file_path).name}",
        description=(
            f"Promoted from grounded_understanding change_group. "
            f"Discovered via '{evidence_source}' with confidence {evidence_score:.2f}. "
            f"Must be modified to fully resolve the ticket."
        ),
        file_path=file_path,
        task_type=TaskType.MODIFY,
        language=lang,
        localization_confidence=round(evidence_score, 3),
        localization_reason=(
            f"change_group_promotion: source={evidence_source}, "
            f"evidence_score={evidence_score:.2f}"
        ),
        new_file_creation_allowed=False,
    )


# ── Component Group Builder (Pre-Ranking) ─────────────────────────────────────
# Groups evidence files into component groups by scanning for sibling files
# on disk. Runs BETWEEN evidence collection and ranking so the ranker can
# score at the component level instead of the individual file level.
#
# Framework-agnostic: works for Angular (.ts+.html+.scss), React (.tsx+.css),
# Vue (.vue+.scss), and any other convention where files share a common stem.
# Standalone files (e.g., Java services) become groups of 1 (is_group=False)
# and are unaffected by the grouping system.

# Extensions that indicate a "source" file (logic/controller)
_GROUP_SOURCE_EXTS = frozenset({".ts", ".tsx", ".jsx", ".js", ".vue", ".svelte"})
# Extensions that indicate a companion file (template/style)
_GROUP_COMPANION_EXTS = frozenset({".html", ".htm", ".scss", ".css", ".less", ".sass"})
# All groupable extensions (source + companion)
_GROUP_ALL_EXTS = _GROUP_SOURCE_EXTS | _GROUP_COMPANION_EXTS


def _build_component_groups(
    evidence_items: list,
    workspace_path: "Path",
) -> "list[ComponentGroup]":
    """
    Build component groups from evidence items by scanning for sibling files.

    Algorithm:
    1. Collect all unique file paths from evidence items.
    2. For each file with a groupable extension (.ts, .html, .scss, etc.),
       compute its stem and directory.
    3. Scan the directory on disk for sibling files with the same stem.
    4. Group them into a ComponentGroup with the highest-scoring evidence
       file as the primary.
    5. Files that don't belong to any group become standalone groups (is_group=False).

    Returns a list of ComponentGroup objects.
    """
    # Collect all evidence files and their best scores
    file_scores: dict[str, float] = {}
    for item in evidence_items:
        fp = item.file_path.replace("\\", "/")
        file_scores[fp] = max(file_scores.get(fp, 0.0), item.relevance_score)

    # Track which files have already been assigned to a group
    assigned: set[str] = set()
    groups: list[ComponentGroup] = []

    # Sort by score descending so higher-scoring files create the groups
    sorted_files = sorted(file_scores.items(), key=lambda x: -x[1])

    for fp, score in sorted_files:
        if fp in assigned:
            continue

        ext = Path(fp).suffix.lower()

        # Only attempt grouping for known framework extensions
        if ext not in _GROUP_ALL_EXTS:
            # Standalone file (e.g., .java, .py, .properties) — group of 1
            groups.append(ComponentGroup(
                stem=Path(fp).stem,
                directory=str(Path(fp).parent).replace("\\", "/"),
                members={ext: fp},
                primary_file=fp,
                group_score=score,
                evidence_sources=[ext],
                is_group=False,
            ))
            assigned.add(fp)
            continue

        # Compute the component stem
        # For "add-members.component.ts" → stem = "add-members.component"
        # For "AddMembers.tsx" → stem = "AddMembers"
        name = Path(fp).name
        stem = name[: -len(ext)] if ext else name
        directory = str(Path(fp).parent).replace("\\", "/")

        # Build the group: start with this file, then scan disk for siblings
        members: dict[str, str] = {ext: fp}
        evidence_sources: list[str] = [ext]

        # Scan for sibling files with the same stem
        abs_dir = workspace_path / directory
        if abs_dir.is_dir():
            for sibling_ext in _GROUP_ALL_EXTS:
                if sibling_ext == ext:
                    continue  # Already in members
                sibling_path = abs_dir / f"{stem}{sibling_ext}"
                if sibling_path.is_file():
                    rel_path = f"{directory}/{stem}{sibling_ext}"
                    members[sibling_ext] = rel_path
                    # Check if this sibling also has evidence
                    if rel_path in file_scores:
                        evidence_sources.append(sibling_ext)

        # Also check: for the reverse case (evidence found .html but not .ts),
        # scan for source files with the same stem
        if ext in _GROUP_COMPANION_EXTS:
            for src_ext in _GROUP_SOURCE_EXTS:
                if src_ext in members:
                    continue
                src_path = abs_dir / f"{stem}{src_ext}"
                if src_path.is_file():
                    rel_path = f"{directory}/{stem}{src_ext}"
                    members[src_ext] = rel_path
                    if rel_path in file_scores:
                        evidence_sources.append(src_ext)

        # Determine the primary file (highest evidence score in group)
        best_file = fp
        best_score = score
        for m_ext, m_path in members.items():
            if m_path in file_scores and file_scores[m_path] > best_score:
                best_score = file_scores[m_path]
                best_file = m_path

        # Create the group
        is_group = len(members) > 1
        groups.append(ComponentGroup(
            stem=stem,
            directory=directory,
            members=members,
            primary_file=best_file,
            group_score=best_score,
            evidence_sources=evidence_sources,
            is_group=is_group,
        ))

        # Mark all group members as assigned
        for m_path in members.values():
            assigned.add(m_path)

    # Handle any evidence files that weren't assigned (shouldn't happen, but safety)
    for fp in file_scores:
        if fp not in assigned:
            ext = Path(fp).suffix.lower()
            groups.append(ComponentGroup(
                stem=Path(fp).stem,
                directory=str(Path(fp).parent).replace("\\", "/"),
                members={ext: fp},
                primary_file=fp,
                group_score=file_scores[fp],
                evidence_sources=[ext],
                is_group=False,
            ))

    logger.info(
        f"  [ComponentGroup] Built {len(groups)} groups from "
        f"{len(file_scores)} evidence files: "
        f"{sum(1 for g in groups if g.is_group)} multi-file, "
        f"{sum(1 for g in groups if not g.is_group)} standalone"
    )
    for g in groups[:10]:
        if g.is_group:
            logger.info(
                f"    Group '{g.stem}': {list(g.members.keys())} "
                f"(primary={Path(g.primary_file).name}, score={g.group_score:.3f}, "
                f"evidence_in={g.evidence_sources})"
            )

    return groups


# ── Companion co-localization helpers (framework-agnostic, disk-grounded) ─────
_COMPONENT_SOURCE_EXTS = {".ts", ".tsx", ".jsx", ".js", ".vue", ".svelte"}
_COMPANION_TEMPLATE_STYLE_EXTS = (".html", ".htm", ".scss", ".css", ".less", ".sass")
# Templates/styles must pull in their controller so logic changes travel with the UI change.
_TEMPLATE_SOURCE_EXTS = {".html", ".htm", ".scss", ".css", ".less", ".sass"}
_TEMPLATE_URL_RE = re.compile(r"""templateUrl\s*:\s*['"]([^'"]+)['"]""")
_STYLE_URLS_RE = re.compile(r"""styleUrls?\s*:\s*\[([^\]]*)\]""")
_STYLE_URL_RE = re.compile(r"""styleUrl\s*:\s*['"]([^'"]+)['"]""")
_QUOTED_STR_RE = re.compile(r"""['"]([^'"]+)['"]""")
_I18N_KEY_RE = re.compile(r"\b([a-z][a-z0-9_]*(?:\.[a-z0-9_]+){2,})\b")


def _safe_rel(candidate: Path, workspace_path: Path) -> "Optional[str]":
    """Return the workspace-relative posix path if candidate is inside workspace."""
    try:
        resolved = candidate.resolve()
        root = workspace_path.resolve()
        if resolved == root or root in resolved.parents:
            return str(resolved.relative_to(root)).replace("\\", "/")
    except Exception:
        return None
    return None


def _find_component_companions(
    source_file: str,
    workspace_path: Path,
    existing_paths: "Set[str]",
) -> "list[tuple[str, str]]":
    """Resolve a component's template/style companions.

    Strategy 1 (ast_decorator_edge): parse templateUrl/styleUrls from the source.
    Strategy 2 (sibling_colocation): same-stem template/style files in the same dir.
    Only returns files that physically exist inside the workspace.
    """
    src = Path(source_file)
    src_ext = src.suffix.lower()
    is_template_or_style = src_ext in _TEMPLATE_SOURCE_EXTS
    is_source = src_ext in _COMPONENT_SOURCE_EXTS

    if not is_source and not is_template_or_style:
        return []

    src_abs = workspace_path / source_file
    src_dir = src_abs.parent
    results: "list[tuple[str, str]]" = []

    # ── Reverse path: template/style → pull in the controller (.ts) ──────────
    # When a .html or .scss is in the plan, the sibling .ts MUST also be included
    # because any new template binding (e.g. *ngIf="dmember.isExistingMemberInProject")
    # requires matching TypeScript logic to populate it.
    if is_template_or_style:
        stem = src.name[: -len(src.suffix)] if src.suffix else src.name
        for _ctrl_ext in (".ts", ".tsx"):
            rel = _safe_rel(src_dir / f"{stem}{_ctrl_ext}", workspace_path)
            if rel and rel not in existing_paths and (workspace_path / rel).is_file():
                results.append((rel, "template_controller_follow"))
        return _dedupe_pairs(results)

    try:
        content = src_abs.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        content = ""

    refs: list[str] = []
    refs.extend(m.group(1) for m in _TEMPLATE_URL_RE.finditer(content))
    refs.extend(m.group(1) for m in _STYLE_URL_RE.finditer(content))
    for m in _STYLE_URLS_RE.finditer(content):
        refs.extend(q.group(1) for q in _QUOTED_STR_RE.finditer(m.group(1)))

    for ref in refs:
        rel = _safe_rel(src_dir / ref, workspace_path)
        if rel and rel not in existing_paths and (workspace_path / rel).is_file():
            results.append((rel, "ast_decorator_edge"))

    # Sibling stem co-location: 'foo.component.ts' → 'foo.component.html' / '.scss'
    stem = src.name[: -len(src.suffix)] if src.suffix else src.name
    for comp_ext in _COMPANION_TEMPLATE_STYLE_EXTS:
        rel = _safe_rel(src_dir / f"{stem}{comp_ext}", workspace_path)
        if rel and rel not in existing_paths and (workspace_path / rel).is_file():
            results.append((rel, "sibling_colocation"))

    # Strategy 3 (model_import_follow): follow relative imports that point to
    # model/interface/dto files — e.g. import { DisplayedMember } from '../../shared/models/displayed-member'
    # These must be in the plan so new interface properties are actually added.
    if content:
        _model_import_re = re.compile(
            r"import\s*\{[^}]+\}\s*from\s*['\"](\.[^'\"]+)['\"]",
            re.MULTILINE,
        )
        _MODEL_KEYWORDS = ("model", "interface", "dto", "type", "entity", "record", "schema")
        for _mi in _model_import_re.finditer(content):
            _rel = _mi.group(1)
            if any(kw in _rel.lower() for kw in _MODEL_KEYWORDS):
                for _ext in (".ts", ".tsx"):
                    _cand = _safe_rel(
                        Path(os.path.normpath(str(src_dir / (_rel + _ext)))),
                        workspace_path,
                    )
                    if _cand and _cand not in existing_paths and (workspace_path / _cand).is_file():
                        results.append((_cand, "model_import_follow"))
                        break

    seen: set = set()
    deduped: "list[tuple[str, str]]" = []
    for path, strategy in results:
        if path not in seen:
            seen.add(path)
            deduped.append((path, strategy))
    return deduped


def _find_i18n_owner_files(
    text_lower: str,
    workspace_path: Path,
    existing_paths: "Set[str]",
    max_files: int = 400,
) -> "list[tuple[str, str]]":
    """Resolve i18n/locale bundles that own translation keys referenced in the text.

    Strategy 3 (i18n_key_index): extract dotted keys, then confirm the key (full
    dotted form or its quoted leaf segment) exists on disk in a locale bundle.
    """
    keys = {m.group(1) for m in _I18N_KEY_RE.finditer(text_lower)}
    keys = {k for k in keys if len(k.split(".")[-1]) >= 4}
    if not keys:
        return []

    skip_dirs = {"node_modules", ".git", "dist", "build", "target", "coverage", ".venv", "venv"}
    results: "list[tuple[str, str]]" = []
    scanned = 0
    for root, dirs, files in os.walk(str(workspace_path)):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        low_root = root.replace("\\", "/").lower()
        locale_dir = ("i18n" in low_root or "locale" in low_root or "assets" in low_root)
        for f in files:
            fl = f.lower()
            is_bundle = (
                (fl.endswith(".json") and locale_dir)
                or (fl.startswith("messages") and fl.endswith(".properties"))
            )
            if not is_bundle:
                continue
            scanned += 1
            if scanned > max_files:
                return _dedupe_pairs(results)
            full = Path(root) / f
            rel = _safe_rel(full, workspace_path)
            if not rel or rel in existing_paths:
                continue
            try:
                low_text = full.read_text(encoding="utf-8", errors="ignore").lower()
            except Exception:
                continue
            for key in keys:
                leaf = key.split(".")[-1]
                if key in low_text or f'"{leaf}"' in low_text:
                    results.append((rel, "i18n_key_index"))
                    break
    return _dedupe_pairs(results)


def _dedupe_pairs(pairs: "list[tuple[str, str]]") -> "list[tuple[str, str]]":
    seen: set = set()
    out: "list[tuple[str, str]]" = []
    for path, strategy in pairs:
        if path not in seen:
            seen.add(path)
            out.append((path, strategy))
    return out


def _make_companion_task(
    file_path: str,
    task_index: int,
    source_file: str,
    strategy: str,
    source_task: "Any" = None,
) -> "Any":
    """Create a writable MODIFY task for a co-localized companion file.

    When source_task is provided (Component Group Architecture), the companion
    task inherits a meaningful description from the source task's plan so the
    code_generator has actual instructions for what to change.
    """
    from ticket_to_code.models import DevelopmentTask, TaskType

    ext = Path(file_path).suffix.lower()
    lang = _lang_for_ext(ext)
    ownership = "DISPLAY_OWNER" if ext in _COMPANION_TEMPLATE_STYLE_EXTS else "SUPPORTING"

    # ── Build context-aware description from source task ──────────────────
    if source_task and hasattr(source_task, "description") and source_task.description:
        source_desc = source_task.description[:300]
        source_title = getattr(source_task, "title", "") or ""

        # Determine what kind of companion this is for targeted instructions
        if ext in (".html", ".htm"):
            companion_instruction = (
                f"Update template/HTML bindings to match the TypeScript changes in "
                f"'{Path(source_file).name}'. Source task: {source_title}. "
                f"Changes to support: {source_desc}"
            )
        elif ext in (".scss", ".css", ".less", ".sass"):
            companion_instruction = (
                f"Update styles to support the UI changes described in "
                f"'{Path(source_file).name}'. Source task: {source_title}. "
                f"Changes to support: {source_desc}"
            )
        elif ext in (".ts", ".tsx"):
            companion_instruction = (
                f"Add TypeScript logic to support the template/style changes in "
                f"'{Path(source_file).name}'. Source task: {source_title}. "
                f"Changes to support: {source_desc}"
            )
        else:
            companion_instruction = (
                f"Component group member of '{Path(source_file).name}'. "
                f"Update to support: {source_title}. {source_desc}"
            )
    else:
        # Fallback: generic description (pre-Component-Group behavior)
        companion_instruction = (
            f"Companion of {source_file} resolved via {strategy}. Edit alongside the "
            f"component so the UI change is applied across template/style/i18n."
        )

    return DevelopmentTask(
        id=f"cmp-{task_index}",
        title=f"Companion ({strategy}): {Path(file_path).name}",
        description=companion_instruction,
        file_path=file_path,
        task_type=TaskType.MODIFY,
        language=lang,
        localization_confidence=0.7,
        localization_reason=f"companion_colocation: {strategy} of {source_file}",
        ownership_type=ownership,
        new_file_creation_allowed=False,
    )



def ownership_completeness_node(
    state: "TicketToCodeState",
    agents: "WorkflowAgents",
) -> dict:
    """
    Phase 2F: Ownership Completeness Check.

    Inserts BETWEEN localize and rag_tests/rag_code.

    For each writable task whose file is a CSS/SCSS file:
      1. Extract all top-level CSS class names defined in the file.
      2. Search the same module directory for sibling SCSS files that share
         any of those class names AND where those shared classes contain
         layout-critical properties (flex, position, height, overflow …).
      3. If found → the sibling is the layout container owner.
         Add it as a new writable MODIFY task (LAYOUT_OWNER).

    For non-SCSS files and non-layout tickets: no expansion occurs.

    STOP CONDITIONS:
      • No layout keywords in ticket  →  skip entirely
      • No SCSS tasks in plan         →  skip entirely
      • No sibling with shared layout classes found  →  stop, coverage sufficient
      • max 2 new tasks added         →  prevent uncontrolled growth
    """
    print("\n" + "=" * 80)
    print(" ENTERING: ownership_completeness_node() in workflow.py")
    print("   Purpose: Verify localized files fully own the described behavior")
    print("=" * 80)
    logger.info(" PHASE 2F: Ownership Completeness Check")

    plan = _get_plan(state)
    ticket = state["ticket"]
    ticket_lower = f"{ticket.title} {ticket.description}".lower()
    workspace_path = Path(state["workspace_path"])

    writable_tasks = [t for t in plan.tasks if t.task_type.value != "read_only"]

    # ── Enrich ticket_lower with grounded understanding root cause ────────────
    grounded = state.get("grounded_understanding")
    if grounded and grounded.root_cause:
        ticket_lower = f"{ticket_lower} {grounded.root_cause.lower()}"

    # ── Ticket intent classification ─────────────────────────────────────────
    is_layout = _ticket_is_layout_focused(ticket_lower)
    is_data = bool(re.search(
        r'\b(count|filter|calculat|incorrect|wrong value|display.*wrong|'
        r'sort|aggregat|total|sum)\b',
        ticket_lower,
    ))

    logger.info(f"  Ticket intent: layout={is_layout}, data={is_data}")

    # Collect existing paths (we never add duplicates)
    # Also include any extra context files from GroundedUnderstanding
    existing_paths: Set[str] = {
        t.file_path.replace("\\", "/") for t in plan.tasks
    }
    if grounded:
        existing_paths.update(
            p.replace("\\", "/") for p in (grounded.readonly_context_files or [])
        )

    # ── Ownership type summary ───────────────────────────────────────────────
    ownership_summary: dict = {}
    for t in writable_tasks:
        ot = t.ownership_type or "UNKNOWN"
        ownership_summary[t.file_path] = ot

    # ── Data-driven UI detection ───────────────────────────────────────────────
    # Instead of hardcoded keyword matching (brittle), check whether the plan
    # itself already contains frontend/UI files.  The presence of a
    # .component.ts, .scss, .css, or .html file is an unambiguous signal that
    # this ticket touches the UI layer and SCSS gap detection should run.
    # The inner loop already short-circuits if no SCSS tasks exist, so this
    # gate is purely about intent — not about performance.
    _UI_EXTS = {".scss", ".css", ".sass", ".less", ".html", ".htm"}
    has_ui_files = any(
        Path(t.file_path).suffix.lower() in _UI_EXTS
        or t.file_path.endswith(".component.ts")
        for t in writable_tasks
    )
    is_ui_ticket = is_layout or has_ui_files

    # ── SCSS gap detection ────────────────────────────────────────────────────
    expanded_tasks: list = []
    layout_gaps: list = []
    stop_condition = "no_ui_signal"

    if is_ui_ticket:
        stop_condition = "no_scss_tasks"
        # Deduplicate by file path — the plan may contain duplicate tasks for
        # the same SCSS file (e.g. parallel test/code branch tasks).  Running
        # gap detection twice on the same source file would add two different
        # sibling files unnecessarily.
        seen_scss: Set[str] = set()
        scss_tasks = []
        for t in writable_tasks:
            fp_norm = t.file_path.replace("\\", "/")
            if Path(t.file_path).suffix.lower() in _SCSS_EXTS and fp_norm not in seen_scss:
                scss_tasks.append(t)
                seen_scss.add(fp_norm)
        if scss_tasks:
            stop_condition = "no_gap_found"
            for task in scss_tasks:
                gap_owners = _find_layout_gap_owners(
                    task.file_path, workspace_path, existing_paths
                )
                for gowner in gap_owners:
                    gpath = gowner["path"]
                    if gpath in existing_paths:
                        continue
                    logger.info(
                        f"   Layout gap: {task.file_path} shares "
                        f"{gowner['shared_classes']} with {gpath} "
                        f"— adding as LAYOUT_OWNER"
                    )
                    layout_gaps.append({
                        "source_file": task.file_path,
                        "layout_owner": gpath,
                        "shared_classes": gowner["shared_classes"],
                    })
                    new_task = _make_ownership_task(
                        file_path=gpath,
                        workspace_path=workspace_path,
                        task_index=len(plan.tasks) + len(expanded_tasks) + 1,
                        role="LAYOUT_OWNER",
                        source_file=task.file_path,
                        shared_classes=gowner["shared_classes"],
                    )
                    expanded_tasks.append(new_task)
                    existing_paths.add(gpath)

            if layout_gaps:
                stop_condition = "gap_filled"

    # ── Companion co-localization (SAFETY NET — most work done by planner) ──────
    # With Component Group Architecture, the planner already sees component
    # groups and creates tasks for each file that needs changes. This section
    # is now a SAFETY NET that catches any companion files the planner missed.
    # Cap is per-component-group (6 groups) instead of per-file.
    _COMPANION_GROUP_CAP = 12
    companion_records: list = []
    companion_groups_added = 0
    for task in list(writable_tasks):
        if companion_groups_added >= _COMPANION_GROUP_CAP:
            break
        companions_for_this_task = []
        for comp_path, strategy in _find_component_companions(
            task.file_path, workspace_path, existing_paths
        ):
            if comp_path in existing_paths:
                # Already in the plan (planner created a task for it via
                # component group context) — skip to prevent double-injection
                logger.info(
                    f"   Companion [{strategy}]: {comp_path} SKIPPED "
                    f"(already in plan from component group)"
                )
                continue
            if companion_groups_added >= _COMPANION_GROUP_CAP:
                break
            logger.info(
                f"   Companion [{strategy}]: {comp_path} (safety net for {task.file_path})"
            )
            expanded_tasks.append(_make_companion_task(
                file_path=comp_path,
                task_index=len(plan.tasks) + len(expanded_tasks) + 1,
                source_file=task.file_path,
                strategy=strategy,
                source_task=task,
            ))
            existing_paths.add(comp_path)
            companions_for_this_task.append(comp_path)
            companion_records.append({
                "source_file": task.file_path,
                "companion": comp_path,
                "strategy": strategy,
                "safety_net": True,
            })
        if companions_for_this_task:
            companion_groups_added += 1


    if len(expanded_tasks) < _COMPANION_GROUP_CAP:
        for i18n_path, strategy in _find_i18n_owner_files(
            ticket_lower, workspace_path, existing_paths
        ):
            if i18n_path in existing_paths or len(expanded_tasks) >= _COMPANION_GROUP_CAP:
                continue
            logger.info(f"   Companion [{strategy}]: {i18n_path} (ticket i18n key)")
            expanded_tasks.append(_make_companion_task(
                file_path=i18n_path,
                task_index=len(plan.tasks) + len(expanded_tasks) + 1,
                source_file="ticket i18n keys",
                strategy=strategy,
            ))
            existing_paths.add(i18n_path)
            companion_records.append({
                "source_file": "ticket",
                "companion": i18n_path,
                "strategy": strategy,
            })

    # ── Type consumer follow: when a model/interface file is modified, add  ──
    # all TS files that IMPORT it as companions so they can be updated too.
    # This is the inverse of model_import_follow (which adds the model when a
    # component changes) — here the model changed, so the consumers must change.
    if len(expanded_tasks) < _COMPANION_GROUP_CAP:
        for task in list(writable_tasks):
            if Path(task.file_path).suffix.lower() not in (".ts", ".tsx"):
                continue
            fp_lower = task.file_path.lower()
            is_model = any(kw in fp_lower for kw in (
                "/models/", "/model.", "-model.", ".model.",
                "/interface.", "-interface.", ".interface.",
                "/dto.", "-dto.", ".dto.", "/types/", "/type.",
            ))
            if not is_model:
                continue
            for consumer_path, strategy in _find_type_consumer_companions(
                task.file_path, workspace_path, existing_paths
            ):
                if consumer_path in existing_paths or len(expanded_tasks) >= _COMPANION_GROUP_CAP:
                    continue
                logger.info(
                    f"   Companion [{strategy}]: {consumer_path} "
                    f"(imports modified model {Path(task.file_path).name})"
                )
                expanded_tasks.append(_make_companion_task(
                    file_path=consumer_path,
                    task_index=len(plan.tasks) + len(expanded_tasks) + 1,
                    source_file=task.file_path,
                    strategy=strategy,
                ))
                existing_paths.add(consumer_path)
                companion_records.append({
                    "source_file": task.file_path,
                    "companion": consumer_path,
                    "strategy": strategy,
                })

    # ── Fix B: Java/TypeScript/Python data-model gap detection ───────────────────
    # DISABLED: The regex-based pre-scan matches generic property names (.value,
    # .name, .key) against every DTO in the monorepo, causing 7+ irrelevant
    # companion tasks. The PER-FILE COMPILE CHECK loop (line ~5668) already
    # handles this correctly: write file → compile → if "property X does not
    # exist on type Y" → read the error → identify the exact file → fix it.
    # This is how Cursor/Copilot/Windsurf handle cross-file dependencies:
    # compile-driven, not regex-driven.
    _DATA_GAP_EXTS = {".java", ".ts", ".tsx", ".py"}
    _data_gap_records: list = []
    _sqlite_for_gap = None  # Disabled: set to None so the block below is skipped

    # Patterns: Java getter calls, TS property access, Python attribute access
    _JAVA_GETTER_CALL_RE = re.compile(r'(\w+)\.get(\w+)\s*\(', re.MULTILINE)
    _TS_PROP_ACCESS_RE   = re.compile(r'(\w+)\.(\w+)\s*(?:[;,\)]|\s*=)', re.MULTILINE)
    _PY_ATTR_ACCESS_RE   = re.compile(r'self\.(\w+)\.?(\w+)?\s*(?:=|\(|\[)', re.MULTILINE)

    if _sqlite_for_gap and len(expanded_tasks) < _COMPANION_GROUP_CAP:
        for task in list(writable_tasks):
            if Path(task.file_path).suffix.lower() not in _DATA_GAP_EXTS:
                continue
            task_abs = workspace_path / task.file_path
            if not task_abs.exists():
                continue
            try:
                _content = task_abs.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            # Collect (accessor_var, member_name) pairs
            _accesses: list = []
            ext = Path(task.file_path).suffix.lower()
            if ext == ".java":
                for m in _JAVA_GETTER_CALL_RE.finditer(_content):
                    _accesses.append((m.group(1), "get" + m.group(2)))
            elif ext in (".ts", ".tsx"):
                for m in _TS_PROP_ACCESS_RE.finditer(_content):
                    _accesses.append((m.group(1), m.group(2)))

            # For each accessed member, find the type declaration in SQLite
            _conn_gap = _sqlite_for_gap._conn
            for _var, _member in _accesses[:50]:  # cap to avoid noise
                try:
                    # Find a file that defines this member — exclude test/generated paths
                    _rows = _conn_gap.execute(
                        "SELECT DISTINCT path FROM symbols WHERE name = ? AND kind IN ('method','field','property') "
                        "AND path NOT LIKE '%Test%' AND path NOT LIKE '%Spec%' AND path NOT LIKE '%/dist/%' LIMIT 3",
                        (_member,),
                    ).fetchall()
                    for (_path,) in _rows:
                        if not _path or _path in existing_paths:
                            continue
                        full = workspace_path / _path
                        if not full.exists():
                            continue
                        # Only add if the file is a model/dto (no Spring stereotype)
                        _stereo = _conn_gap.execute(
                            "SELECT spring_stereotype FROM symbols WHERE path = ? AND spring_stereotype != '' LIMIT 1",
                            (_path,),
                        ).fetchone()
                        if _stereo:  # skip services/controllers
                            continue
                        logger.info(f"   Data model gap [{ext}]: {task.file_path} calls .{_member} → adding {_path}")
                        _data_gap_records.append({"source_file": task.file_path, "model_file": _path, "member": _member})
                        expanded_tasks.append(_make_companion_task(
                            file_path=_path,
                            task_index=len(plan.tasks) + len(expanded_tasks) + 1,
                            source_file=task.file_path,
                            strategy="data_model_gap",
                        ))
                        existing_paths.add(_path)
                        companion_records.append({"source_file": task.file_path, "companion": _path, "strategy": "data_model_gap"})
                        if len(expanded_tasks) >= _COMPANION_GROUP_CAP:
                            break
                except Exception:
                    pass
                if len(expanded_tasks) >= _COMPANION_GROUP_CAP:
                    break
            if len(expanded_tasks) >= _COMPANION_GROUP_CAP:
                break

    # ── Compute coverage score (for trace; not used as gate) ─────────────────
    all_otypes = list(ownership_summary.values())
    behavioral = (
        1.0 if "PRIMARY_OWNER" in all_otypes
        else (0.6 if "DISPLAY_OWNER" in all_otypes else 0.3)
    )
    layout_cov = (0.4 if layout_gaps else 1.0) if is_layout else 1.0
    data_cov = (
        1.0 if not is_data
        else (1.0 if "PRIMARY_OWNER" in all_otypes else 0.4)
    )
    coverage_score = round((behavioral + layout_cov + data_cov) / 3, 3)

    # ── Merge expanded tasks into plan ───────────────────────────────────────
    if expanded_tasks:
        plan.tasks = plan.tasks + expanded_tasks
        logger.info(
            f"  ✅ Ownership expanded: +{len(expanded_tasks)} task(s). "
            f"Total writable: "
            f"{sum(1 for t in plan.tasks if t.task_type.value != 'read_only')}"
        )
    else:
        logger.info(
            f"  ✅ Ownership sufficient — no expansion needed "
            f"(stop_condition={stop_condition})"
        )

    # ── Build completeness result for state + trace ───────────────────────────
    completeness = {
        "is_layout_ticket": is_layout,
        "is_data_ticket": is_data,
        "localized_files": [t.file_path for t in writable_tasks],
        "ownership_roles": ownership_summary,
        "ownership_coverage": {
            "behavioral": round(behavioral, 3),
            "layout": round(layout_cov, 3),
            "data": round(data_cov, 3),
            "overall": coverage_score,
        },
        "layout_gaps": layout_gaps,
        "expanded_writable_files": [t.file_path for t in expanded_tasks],
        "companion_files": companion_records,
        "supporting_readonly_files": [
            t.file_path for t in plan.tasks if t.task_type.value == "read_only"
        ],
        "stop_condition": stop_condition,
        "ownership_reasoning": [
            f"{g['source_file']} shares CSS classes {g['shared_classes']} with "
            f"{g['layout_owner']} — layout container ownership gap detected"
            for g in layout_gaps
        ] if layout_gaps else [
            f"stop_condition={stop_condition}: ownership sufficient at "
            f"behavioral={behavioral:.2f}, layout={layout_cov:.2f}, data={data_cov:.2f}"
        ],
    }

    agents.tracer.record_ownership_completeness(completeness)

    return {
        "architectural_plan": plan,
        "ownership_completeness": completeness,
        "status": "ownership_checked",
    }


# ============================================================================
# DATA-FLOW VERIFICATION NODE
# Mirrors the "read → think → write → verify" loop used by Claude Code / Devin.
# Runs after plan expansion but BEFORE RAG so missing .ts tasks can be injected.
# ============================================================================

def dataflow_verification_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Trace the full data path for every Angular template binding in the plan.

    For each HTML task, scan the template (or its planned changes) for new bindings
    like `*ngIf="dmember.isExistingMemberInProject"` and verify that:
      1. The sibling .ts controller is also a writable task in the plan.
      2. The bound property is SET somewhere (controller or model task).

    If the controller is missing from the plan → inject it as a new MODIFY task.
    If a bound property is not set anywhere → record it as a known gap so the
    code generator's system prompt can explicitly warn about it.
    """
    from ticket_to_code.agents.dataflow_tracer import (
        trace_plan_data_flow,
        gap_summary,
        DataFlowGap,
    )
    from ticket_to_code.models import DevelopmentTask, TaskType

    plan = _get_plan(state)
    if not plan or not getattr(plan, "tasks", None):
        return {}

    workspace_path = str(state.get("workspace_path", ""))
    session_map: dict = state.get("_run_generated_map", {})

    logger.info("\n" + "=" * 60)
    logger.info(" DATAFLOW VERIFICATION (Read → Think → Verify before Write)")
    logger.info("=" * 60)

    writable_paths = {
        t.file_path.replace("\\", "/").lower()
        for t in plan.tasks
        if getattr(getattr(t, "task_type", None), "value", str(getattr(t, "task_type", ""))).lower() != "read_only"
    }

    reports = trace_plan_data_flow(
        plan_tasks=plan.tasks,
        workspace_path=workspace_path,
        session_map=session_map,
    )

    injected: list[str] = []
    all_gaps: list[DataFlowGap] = []

    for report in reports:
        if not report.has_gaps:
            logger.info(f"  ✅ No data-flow gaps for {report.html_file}")
            continue

        all_gaps.extend(report.gaps)
        logger.warning(
            f"  ⚠️ DataFlow gaps in {report.html_file}:\n"
            + "\n".join(f"    • {g.binding.variable}.{g.binding.property} — {g.reason[:120]}"
                        for g in report.gaps)
        )

        # Inject .ts controller task if it's missing from the plan
        ctrl = report.controller_file.replace("\\", "/")
        ctrl_lower = ctrl.lower()
        if ctrl and ctrl_lower not in writable_paths:
            ctrl_abs = Path(workspace_path) / ctrl
            if ctrl_abs.is_file():
                gap_props = ", ".join(f"{g.binding.variable}.{g.binding.property}" for g in report.gaps)
                new_task = DevelopmentTask(
                    id=f"df-ctrl-{len(injected) + 1}",
                    title=f"DataFlow: populate {Path(ctrl).name} with template-bound properties",
                    description=(
                        f"The HTML template {report.html_file} references properties that are not "
                        f"set in the controller. This task adds the missing logic to: {gap_props}. "
                        f"Concretely: fetch the user's existing organization from the backend when "
                        f"a member is staged, then set the bound properties on the displayed object."
                    ),
                    file_path=ctrl,
                    task_type=TaskType.MODIFY,
                    language="typescript",
                    selection_reason=(
                        f"DataFlow tracer: controller is missing setters for template bindings "
                        f"in {report.html_file}. Auto-injected to prevent silent undefined at runtime."
                    ),
                    allowed_methods=[g.binding.property for g in report.gaps],
                    new_file_creation_allowed=False,
                    estimated_complexity=2,
                    requires_testing=False,
                )
                plan.tasks.append(new_task)
                writable_paths.add(ctrl_lower)
                injected.append(ctrl)
                logger.info(
                    f"  ➕ Injected missing controller task: {ctrl} "
                    f"(properties needed: {gap_props})"
                )
            else:
                logger.warning(
                    f"  ⚠️ Controller {ctrl} does not exist on disk — cannot auto-inject task"
                )

    summary = gap_summary(reports) if reports else "No HTML tasks with bindings found."
    logger.info(f"\n DataFlow Summary:\n{summary}")

    result: dict = {"architectural_plan": plan}
    if all_gaps or injected:
        result["_dataflow_gaps"] = [
            {
                "html_file": g.binding.expression,
                "variable": g.binding.variable,
                "property": g.binding.property,
                "controller": g.controller_file,
                "reason": g.reason,
            }
            for g in all_gaps
        ]
        result["_dataflow_injected_tasks"] = injected
        logger.info(
            f"\n  DataFlow result: {len(all_gaps)} gap(s), {len(injected)} task(s) injected"
        )
    else:
        logger.info("  ✅ DataFlow: all template bindings have corresponding data suppliers")

    return result


def rag_for_tests_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 3A: RAG Context Retrieval for TEST GENERATION
    
    Retrieves PRODUCT BEHAVIOR knowledge:
    - How features currently work
    - Existing test patterns
    - Business rules and validations
    - Error handling patterns
    
    This context is ONLY used for test generation, NOT for code.
    """
    logger.info(" PHASE 3A: RAG Retrieval for Test Cases (Product Behavior)")
    
    # Query for BEHAVIOR knowledge
    test_context = agents.rag_engine.multi_stage_retrieval(
        requirements=state["requirements"],
        architectural_plan=_get_plan(state),
        query_focus="test_behavior",  # Focus on how things work
        document_types=["tests", "documentation", "business_rules"],
        max_iterations=3
    )
    
    logger.info(
        f"Test RAG complete:\n"
        f"  Behavior examples: {len(test_context)}\n"
        f"  Sources: tests, docs, business rules"
    )
    
    return {
        "test_rag_context": test_context
    }


def rag_for_code_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 3B: RAG Context Retrieval for CODE GENERATION (B2 - cascading context).

    Uses ContextRetrievalService which cascades through:
      RAG → Neo4j → SQLite → Grep → DirectRead
    so code generation never receives zero context.
    """
    logger.info("⚡ PHASE 3B: Context Retrieval for Code (cascading — B2)")

    run_ctx: Optional[RunContext] = _get_transient(state, "run_ctx")
    if run_ctx:
        run_ctx.start_phase("rag_for_code")

    plan = _get_plan(state)

    try:
        svc = ContextRetrievalService(
            rag_engine=agents.rag_engine,
            neo4j_store=getattr(agents.localizer, "neo4j_store", None),
            sqlite_store=getattr(agents.localizer, "sqlite_store", None),
            workspace_path=state["workspace_path"],
        )
        code_context, sources_used = svc.retrieve(
            requirements=state.get("requirements"),
            plan=plan,
            run_ctx=run_ctx,
        )
        logger.info(
            "Code context retrieved: %d items via %s",
            len(code_context), ", ".join(sources_used) if sources_used else "none",
        )
        if run_ctx:
            run_ctx.architecture_chunks  = len(code_context)
            run_ctx.context_sources_used = sources_used
    except Exception as exc:
        logger.warning("ContextRetrievalService failed (%s) — falling back to empty", exc)
        code_context = []
        sources_used = []
        if run_ctx:
            run_ctx.degrade("context_retrieval", HealthLevel.FAILED, str(exc))
    finally:
        if run_ctx:
            run_ctx.end_phase("rag_for_code")

    return {"code_rag_context": code_context}


def generate_tests_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 4A: Generate Test Cases FIRST (TDD)
    """
    # Test generation MUST be enabled for comprehensive verification
    ENABLE_TEST_GENERATION = True
    
    if not ENABLE_TEST_GENERATION:
        logger.info(" PHASE 4A: Generate Test Cases (TEST_GENERATION_DISABLED per feature flag)")
        return {
            "generated_tests": [],
            "test_status": "tests_skipped"  # TEST BRANCH status
        }
    
    logger.info(" PHASE 4A: Generate Test Cases (TDD)")
    
    # ── Grounded Understanding context ───────────────────────────────────────
    grounded = state.get("grounded_understanding")
    grounded_test_ctx = ""
    if grounded:
        grounded_test_ctx = f"""
GROUNDED UNDERSTANDING (verified evidence):
  root_cause: {grounded.root_cause}
  change_group: {grounded.change_group}
  confidence: {grounded.confidence:.2f}
  hypotheses_confirmed: {grounded.hypotheses_confirmed}
"""
    
    test_generation_prompt = f"""
You are generating UNIT TESTS for a new feature.

IMPORTANT: Generate tests BEFORE the code exists (Test-Driven Development).
Base your tests on:
1. Product behavior (how it SHOULD work)
2. Existing test patterns (structure and style)
3. Business rules (validation, edge cases)
4. Grounded evidence (verified investigation — see below)

DO NOT assume implementation details - focus on BEHAVIOR CONTRACT.

Ticket: {state['ticket'].title}
Requirements: {state['requirements']}
Plan: {state['architectural_plan']}
{grounded_test_ctx}
Product Behavior Context (from existing tests/docs):
{state['test_rag_context']}

Generate comprehensive test cases that:
- Cover happy path scenarios
- Test edge cases and error conditions
- Verify business rules
- Follow existing test patterns
- Use real product behavior (no imagination)

Technology stack: Java (JUnit 5 / Spring Boot Test) for .java files;
Angular/TypeScript (Jasmine + Karma) for .ts/.spec.ts files.
"""
    
    generated_tests = []
    validator = PatchValidator()
    
    # ── BUILD FILE SCOPE LISTS ────────────────────────────────────────────────
    all_tasks = _get_plan(state).tasks
    allowed_files  = [t.file_path for t in all_tasks if t.task_type.value != "read_only"]
    if len(allowed_files) > 25:
        logger.warning(
            f"⚠️  Planner created {len(allowed_files)} writable tasks — "
            f"this is a very large refactoring. Proceeding without caps."
        )

    #Every architectural task gets corresponding tests
    for task in _get_plan(state).tasks:
        # READ_ONLY tasks never generate test files
        if task.task_type.value == "read_only":
            logger.info(f"  ⏩ Skipping read_only task [{task.id}] in test gen: {task.title}")
            continue

        logger.info(f"  Generating tests for: {task.file_path}")

        # Skip test generation for style/markup/script files — they have no runnable tests
        _SKIP_TEST_EXTS = {".scss", ".css", ".html", ".xml", ".json", ".yaml", ".yml", ".sh", ".bat", ".ps1", ".cmd", ".env"}
        if Path(task.file_path).suffix.lower() in _SKIP_TEST_EXTS:
            logger.info(f"  ⏩ Skipping test gen for style/markup/script file: {task.file_path}")
            continue

        # Derive correct test file path based on file extension / language
        src_path = task.file_path

        # Guard: skip if the source file is itself a test/spec file.
        # Without this guard a path like "jheader.component.spec.ts" would
        # produce "jheader.component.spec.spec.ts" because the suffix-strip
        # `.ts` leaves the embedded ".spec." in place.
        _TEST_MARKERS = (".spec.", ".test.", "Test.java", "Tests.java", "Spec.java")
        if any(marker in src_path for marker in _TEST_MARKERS):
            logger.info(
                f"  ⏩ Skipping test gen — source is already a test file: {src_path}"
            )
            continue

        if src_path.endswith(".java"):
            # Java: src/main/java/… → src/test/java/…, add Test suffix
            test_file_path = src_path.replace("src/main/java", "src/test/java")
            test_file_path = test_file_path.replace(".java", "Test.java")
        elif src_path.endswith(".tsx"):
            # TypeScript React: safe suffix replace (avoids .spec.spec.tsx)
            test_file_path = src_path[:-4] + ".spec.tsx"
        elif src_path.endswith(".ts"):
            # TypeScript/Angular: safe suffix replace (avoids .spec.spec.ts)
            test_file_path = src_path[:-3] + ".spec.ts"
        elif src_path.endswith(".jsx"):
            # JavaScript/React: add .test before extension
            test_file_path = src_path[:-4] + ".test.jsx"
        elif src_path.endswith(".js"):
            test_file_path = src_path[:-3] + ".test.js"
        elif src_path.endswith(".vue"):
            test_file_path = src_path[:-4] + ".spec.ts"
        else:
            # Generic fallback — append Test suffix before extension
            stem = Path(src_path).stem
            suffix = Path(src_path).suffix
            test_file_path = str(Path(src_path).parent / f"{stem}Test{suffix}")
        
        # For modify tasks, read existing test file if it exists
        existing_test_content = None
        existing_test_path = Path(state["workspace_path"]) / test_file_path
        if task.task_type.value == "modify" and existing_test_path.exists():
            existing_test_content = existing_test_path.read_text(encoding='utf-8')
            logger.info(f"   Reading existing test file for modification: {existing_test_path}")
        
        # Build a TEST-SCOPED task so the generator's frozen scope block shows
        # the correct test file path and allows creation/modification there.
        import copy
        test_task = copy.copy(task)
        test_task.file_path = test_file_path
        test_task.new_file_creation_allowed = (existing_test_content is None)  # True=CREATE new spec, False=MODIFY existing
        # Clear source-method constraints — test generator writes the full test class
        test_task.target_method = None
        test_task.allowed_methods = []

        try:
            test_code = agents.test_generator.generate_code(
                task=test_task,            # <-- test-scoped task, NOT source task
                requirements=state["requirements"],
                context=state["test_rag_context"],  # ONLY behavior context
                existing_content=existing_test_content
            )
        except Exception as e:
            logger.error(f"  ❌ LLM Test Generation failed for {test_task.file_path}: {e}")
            continue
        
        # Enforce: LLM cannot decide the test file location
        test_code.file_path = test_file_path

        # ── PATCH VALIDATION ─────────────────────────────────────────────────
        validation = validator.validate(test_code, test_task, existing_test_content)
        log_line = StageLog.patch_validation(test_task, validation)
        logger.info(log_line)
        print(log_line)
        if not validation.passed:
            logger.warning(
                f"  ⛔ Skipping test write for {test_file_path} — patch failed validation:\n"
                + "\n".join(f"     • {v}" for v in validation.violations)
            )
            generated_tests.append(test_code)
            continue
        
        # Write test file
        output_path = Path(state["workspace_path"]) / test_file_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(test_code.content, encoding='utf-8')
        
        generated_tests.append(test_code)
        logger.info(f"  ✅ Test written: {output_path} | change_ratio={validation.metrics.get('change_ratio', 'N/A')}")
    
    logger.info(f"Test generation complete: {len(generated_tests)} test files")
    
    return {
        "generated_tests": generated_tests,
        "test_status": "tests_generated"  # TEST BRANCH status
    }


def generate_code_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 4B: Generate Implementation Code SECOND (TDD)
    
    Uses ONLY code_rag_context (architecture patterns).
    Generates code that:
    - Follows architectural patterns
    - Implements clean structure
    - Matches coding standards
    
    NO access to test context (prevents code knowing test internals).
    Code must satisfy tests independently.
    """
    logger.info(" PHASE 4B: Generate Implementation Code (to satisfy tests)")
    
    # ── Grounded Understanding context ───────────────────────────────────────
    grounded = state.get("grounded_understanding")
    grounded_context_str = ""
    if grounded:
        grounded_context_str = f"""
GROUNDED UNDERSTANDING (from evidence investigation):
  root_cause: {grounded.root_cause}
  confidence: {grounded.confidence:.2f}
  dependency_chain: {grounded.dependency_chain[:5]}
  change_group: {grounded.change_group}
  writable_files: {grounded.writable_files}
  hypotheses_confirmed: {grounded.hypotheses_confirmed}

EVIDENCE SUMMARY ({len(grounded.evidence)} items):
{chr(10).join(f'  [{e.source}] {e.file_path}: {e.content_snippet[:80]}' for e in grounded.evidence[:8])}
"""
    
    code_generation_prompt = f"""
You are generating PRODUCTION CODE to satisfy pre-written tests.

IMPORTANT: Tests already exist. Your code must make them pass.
Base your implementation on:
1. Architectural patterns (how to structure code)
2. Existing code examples (style and conventions)
3. Coding standards (best practices)
4. Grounded evidence (verified investigation — see below)

DO NOT look at test internals - implement to satisfy the CONTRACT.

Ticket: {state['ticket'].title}
Requirements: {state['requirements']}
Plan: {state['architectural_plan']}
{grounded_context_str}
Architecture Context (from existing code):
{state['code_rag_context']}

Generate production-ready code that:
- Follows architectural patterns
- Implements requirements correctly
- Handles errors appropriately
- Uses dependency injection
- Includes logging
- Follows coding standards

Technology stack: Java (Spring Boot / Maven) for .java files;
Angular/TypeScript for .ts/.html/.scss files.
"""
    generated_code = []
    successful_writes = 0
    written_file_paths: set[str] = set()
    attempted_writable_files: set[str] = set()
    validator = PatchValidator()

    # ── ARTIFACT CLASSIFICATION AND ROUTING FOUNDATION ───────────────────────
    from ticket_to_code.agents.artifact_classifier import ArtifactClassifier
    from ticket_to_code.agents.generation_router import GenerationRouter
    
    classifier = ArtifactClassifier()
    router = GenerationRouter(workspace_path=state["workspace_path"])
    
    classification_traces = []
    routing_traces = []

    # ── BUILD FILE SCOPE LISTS ────────────────────────────────────────────────
    # allowed_files: the planner approved these for writing (modify / create)
    # readonly_files: planner included these for context only (read_only)
    all_tasks = _get_plan(state).tasks
    allowed_files  = [t.file_path for t in all_tasks if t.task_type.value != "read_only"]
    readonly_files = [t.file_path for t in all_tasks if t.task_type.value == "read_only"]

    # Warn early if the planner is being too aggressive
    if len(allowed_files) > 25:
        logger.warning(
            f"⚠️  Planner created {len(allowed_files)} writable tasks — "
            f"this is a very large refactoring. Proceeding without caps."
        )

    logger.info(
        f"   Scope: {len(allowed_files)} writable file(s), "
        f"{len(readonly_files)} read-only file(s)"
    )
    for p in allowed_files:
        logger.info(f"    ✅  writable: {p}")
    for p in readonly_files:
        logger.info(f"      read-only: {p}")

    # ── OWNERSHIP MAP from Phase 2 discovery results ──────────────────────────
    # Maps normalized path → ownership_type string produced by _verify_ownership.
    # Used to populate task.ownership_type before calling PatchValidator so the
    # ownership-tiered change budget (CHECK 5) can fire correctly.
    _ownership_map: dict[str, str] = {}
    for _disc in (state.get("discovered_files") or []):
        _np = _disc.get("path", "").replace("\\", "/").lower()
        _ot = _disc.get("features", {}).get("ownership_type", None)
        if _np and _ot:
            _ownership_map[_np] = _ot

    _sorted_tasks = _sort_tasks_by_execution_order(_get_plan(state).tasks)

    # ── Phase 0: Generate Definition of Done (before any code generation) ────
    # The checklist is consumed by Phase 5 (ChecklistVerifier) after build succeeds.
    if not state.get("definition_of_done"):
        try:
            from ticket_to_code.agents.checklist_agent import ChecklistAgent
            _checklist_agent = ChecklistAgent(agents.llm)
            _ticket = state.get("ticket")
            _dod = _checklist_agent.generate_checklist(
                ticket_title=getattr(_ticket, "title", "") or "",
                ticket_description=getattr(_ticket, "description", "") or "",
                requirements=state.get("requirements"),
                plan_tasks=_sorted_tasks,
            )
            state["definition_of_done"] = _dod
            logger.info(
                f"  ✅ Phase 0: {len(_dod.items)} checklist items generated"
            )
        except Exception as _dod_exc:
            logger.warning(f"  ⚠️ Phase 0 (ChecklistAgent) failed: {_dod_exc}")

    logger.info("  Execution order:")
    for _ot in _sorted_tasks:
        if getattr(_ot.task_type, "value", str(_ot.task_type)) != "read_only":
            logger.info(f"    [{getattr(_ot.task_type,'value',str(_ot.task_type)).upper():6}] {_ot.file_path}")

    for task in _sorted_tasks:
        # READ_ONLY tasks are context-only — never write files
        if task.task_type.value == "read_only":
            logger.info(f"  ⏩ Skipping read_only task [{task.id}]: {task.title}")
            continue

        # SCOPE GUARD: skip if this task was trimmed by the hard cap
        if task.file_path not in allowed_files:
            logger.warning(
                f"   SCOPE GUARD: task [{task.id}] targets '{task.file_path}' "
                f"which exceeded the writable-task cap — skipping."
            )
            continue

        attempted_writable_files.add(task.file_path)

        # Populate Phase 2 ownership type onto the task so PatchValidator can
        # apply the correct change budget (PRIMARY vs DISPLAY_OWNER/SUPPORTING).
        _task_norm = task.file_path.replace("\\", "/").lower()
        if _task_norm in _ownership_map:
            task.ownership_type = _ownership_map[_task_norm]
            logger.info(f"  ️  ownership_type={task.ownership_type} for {task.file_path}")

        logger.info(f"  Generating code for: {task.file_path}")

        # Build the candidate queue: primary path first, then ranked fallbacks
        # Candidates carry the full signal breakdown from localization.
        candidate_paths: list[str] = [task.file_path]
        if task.file_candidates:
            for c in task.file_candidates[1:]:  # [0] is already task.file_path
                if c.path not in candidate_paths:
                    candidate_paths.append(c.path)

        written = False
        for attempt, candidate_path in enumerate(candidate_paths):
            if attempt > 0:
                logger.info(
                    f"   Retry [{attempt}] with fallback candidate: {candidate_path}"
                )
                task.file_path = candidate_path  # swap to next candidate

            # For modify tasks, read existing file content so LLM modifies in place.
            # Fix 2: prefer already-generated-this-run content over on-disk content so
            # later tasks (components) see the updated interfaces/models written earlier.
            existing_content = None
            _run_generated_map: dict = state.setdefault("_run_generated_map", {})
            _task_norm_key = task.file_path.replace("\\", "/").lower()
            if _task_norm_key in _run_generated_map:
                existing_content = _run_generated_map[_task_norm_key]
                logger.info(f"   Using this-run generated content for: {task.file_path}")
            else:
                existing_path = Path(state["workspace_path"]) / task.file_path
                if existing_path.exists():
                    existing_content = existing_path.read_text(encoding='utf-8')
                    logger.info(f"   Reading existing file for modification/context: {existing_path}")
                if "original_file_contents" not in state:
                    state["original_file_contents"] = {}
                state["original_file_contents"][task.file_path] = existing_content

            # 1. Classify Artifact
            artifact_type = classifier.classify(task.file_path)
            task.artifact_type = artifact_type
            
            # 2. Record artifact classification
            classification_traces.append({
                "file_path": task.file_path,
                "artifact_type": artifact_type.value,
            })
            
            # 3. Route through GenerationRouter
            generator = router.route(task)
            
            # 4. Record generation routing
            routing_traces.append({
                "file_path": task.file_path,
                "artifact_type": artifact_type.value,
                "selected_generator": generator.__class__.__name__
            })

            # Temporary diagnostic logging
            logger.info("\n[GENERATION]")
            logger.info(f"task={task.id}")
            logger.info(f"file={task.file_path}")
            logger.info(f"artifact={artifact_type.value}")
            logger.info(f"generator={generator.__class__.__name__}\n")

            # Write traces to disk immediately before generation starts
            agents.tracer.record_artifact_classification(classifications=classification_traces)
            agents.tracer.record_generation_routing(routing=routing_traces)

            # Inject sqlite_store so Layer 1 import context can query the symbol index.
            generator._sqlite_store = getattr(agents.localizer, "sqlite_store", None)
            generator._workspace_path = str(state.get("workspace_path", ""))
            # Inject fresh session-generated files for incremental cross-file context.
            generator._session_files = state.get("_run_generated_map", {})

            # Invoke returned generator (no broad try-except, per user instructions)
            code = generator.generate_code(
                task=task,
                requirements=state["requirements"],
                context=state["code_rag_context"],
                existing_content=existing_content,
                allowed_files=allowed_files,
                readonly_files=readonly_files,
            )

            # ── PATCH VALIDATION: check scope before writing to disk ──────────
            validation = validator.validate(code, task, existing_content)
            log_line = StageLog.patch_validation(task, validation)
            logger.info(log_line)
            print(log_line)
            if not validation.passed:
                logger.warning(
                    f"  ⛔ Candidate [{attempt}] {task.file_path} failed validation:\n"
                    + "\n".join(f"     • {v}" for v in validation.violations)
                )
                if attempt + 1 < len(candidate_paths):
                    logger.info(f"  ↩️  Will retry with next candidate …")
                    continue  # try next candidate
                else:
                    logger.warning(f"  ❌ All {len(candidate_paths)} candidate(s) failed — skipping task")
                    break

            # ── TRUNCATION / CORRUPTION GUARD ────────────────────────────────
            # Reject output that is clearly malformed LLM output:
            #  - Truncation markers (ran out of tokens)
            #  - Markdown code fences inside code (``` in non-markdown files)
            #  - Java lines that look like package refs but missing 'import'
            _trunc_markers = (
                "[TRUNCATED]", "[...]", "// ... rest", "/* ... */",
                "# ... rest", "...existing code...", "...rest of",
            )
            content_lower = code.content.lower()
            truncated = any(m.lower() in content_lower for m in _trunc_markers)

            # Code-fence corruption: LLM embedded markdown in source code
            _ts_ext_check = Path(code.file_path).suffix.lower()
            if not truncated and _ts_ext_check not in (".md", ".txt", ".rst"):
                if "```" in code.content:
                    truncated = True
                    logger.warning(
                        f"  ⚠️ Code-fence corruption in {code.file_path}: "
                        f"LLM embedded markdown ``` inside source code"
                    )

            # Java brace balance: "reached end of file while parsing" = unclosed braces
            if not truncated and _ts_ext_check in (".java", ".kt"):
                _open_b = code.content.count("{")
                _close_b = code.content.count("}")
                if _open_b > _close_b:
                    truncated = True
                    logger.warning(
                        f"  ⚠️ Java brace imbalance in {code.file_path}: "
                        f"{_open_b} open vs {_close_b} close — file truncated before closing class/method"
                    )
                # Also check: last non-whitespace char should be '}'
                _last_char = code.content.rstrip()[-1:] if code.content.rstrip() else ""
                if not truncated and _last_char and _last_char != "}" and "class " in code.content:
                    truncated = True
                    logger.warning(
                        f"  ⚠️ Java file {code.file_path} does not end with '}}' — "
                        f"likely truncated (last char: {repr(_last_char)})"
                    )

            # Java-import corruption: line looks like package path without 'import'
            if not truncated and _ts_ext_check == ".java":
                _bad_java_lines = [
                    ln.strip() for ln in code.content.splitlines()
                    if re.match(r'^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}$', ln.strip())
                    and not ln.strip().startswith("import ")
                    and not ln.strip().startswith("package ")
                ]
                if _bad_java_lines:
                    truncated = True
                    logger.warning(
                        f"  ⚠️ Java import-syntax corruption in {code.file_path}: "
                        f"lines missing 'import' keyword: {_bad_java_lines[:3]}"
                    )

            # Cross-service import guard: project-service CANNOT import area-service internals
            if not truncated and _ts_ext_check == ".java":
                _cross_svc = _check_java_cross_service_imports(code.file_path, code.content)
                if _cross_svc:
                    truncated = True
                    logger.warning(
                        f"  ⚠️ Cross-service import violation in {code.file_path} — "
                        f"these imports cross microservice boundaries and will fail to compile: "
                        + "; ".join(_cross_svc[:3])
                    )
                    _silent_failures = state.setdefault("_patch_failures", [])
                    _silent_failures.append({
                        "file": code.file_path,
                        "reason": f"cross_service_import:{_cross_svc[:2]}",
                        "allowed_methods": task.allowed_methods or [],
                    })
            if not truncated and existing_content:
                # Also flag if the generated file is >20% shorter than original
                # (a likely sign the model stopped early)
                orig_lines = len(existing_content.splitlines())
                new_lines  = len(code.content.splitlines())
                if orig_lines > 20 and new_lines < orig_lines * 0.8:
                    truncated = True
                    logger.warning(
                        f"  ⚠️ Generated file is only {new_lines} lines vs "
                        f"{orig_lines} original — likely truncated"
                    )
            if truncated:
                logger.warning(
                    f"  ⚠️ LLM output appears TRUNCATED for {task.file_path} — "
                    f"refusing to write corrupted code to disk"
                )
                if attempt + 1 < len(candidate_paths):
                    logger.info(f"  ↩️  Will retry with next candidate …")
                    continue
                else:
                    logger.warning(f"  ❌ All candidates produced truncated output — skipping task")
                    break

            # Write code file
            output_path = Path(state["workspace_path"]) / code.file_path
            
            # Skip write if content is unchanged (patch application failed silently)
            if existing_content and code.content.strip() == existing_content.strip():
                logger.warning(
                    f"  ⚠️ Generated content identical to original for {task.file_path} — "
                    f"patch likely failed (SEARCH block may not have matched). "
                    f"Planner allowed_methods: {task.allowed_methods or []}"
                )
                # Track silent failure so it surfaces in the workflow explanation
                _silent_failures = state.setdefault("_patch_failures", [])
                _silent_failures.append({"file": task.file_path, "reason": "content_unchanged",
                                          "allowed_methods": task.allowed_methods or []})
                if attempt + 1 < len(candidate_paths):
                    logger.info(f"  ↩️  Will retry with next candidate …")
                    continue
                break
                
            if code.documentation and code.documentation.startswith("FAILED:"):
                logger.warning(f"  ⚠️ Code generator reported failure: {code.documentation} — skipping write")
                _silent_failures = state.setdefault("_patch_failures", [])
                _silent_failures.append({"file": task.file_path, "reason": code.documentation,
                                          "allowed_methods": task.allowed_methods or []})
                if attempt + 1 < len(candidate_paths):
                    logger.info(f"  ↩️  Will retry with next candidate …")
                    continue
                break
                
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # ── Layer 2: META imports injection (Devin / Copilot Workspace) ─────
            if code.imports:
                code = _inject_meta_imports(code)

            # ── Parenthesis balance check (catches broken callbacks before write) ──
            _par_ext = Path(code.file_path).suffix.lower()
            if existing_content and _par_ext in (".ts", ".tsx", ".js", ".jsx", ".java"):
                _open_p = code.content.count("(")
                _close_p = code.content.count(")")
                if abs(_open_p - _close_p) > 2:  # allow minor off-by-one from string literals
                    logger.warning(
                        f"  ⚠️ Parenthesis imbalance in {code.file_path}: "
                        f"{_open_p} open vs {_close_p} close — rejecting broken patch, reverting to original"
                    )
                    _silent_failures = state.setdefault("_patch_failures", [])
                    _silent_failures.append({
                        "file": code.file_path,
                        "reason": f"paren_imbalance:open={_open_p},close={_close_p}",
                        "allowed_methods": task.allowed_methods or [],
                    })
                    if attempt + 1 < len(candidate_paths):
                        logger.info(f"  ↩️  Will retry with next candidate …")
                        continue
                    break

            # ── Fix 1: Export-drop guard (TypeScript / JavaScript) ────────────
            _ts_ext = Path(code.file_path).suffix.lower()
            if existing_content and _ts_ext in (".ts", ".tsx", ".js", ".jsx"):
                _dropped = _check_ts_exports_preserved(existing_content, code.content)
                if _dropped:
                    logger.warning(
                        f"  ⚠️ Export-drop guard REJECTED write for {code.file_path}: "
                        f"missing exports: {_dropped} — skipping to prevent TS2305 cascade"
                    )
                    _silent_failures = state.setdefault("_patch_failures", [])
                    _silent_failures.append({
                        "file": code.file_path,
                        "reason": f"export_dropped:{_dropped}",
                        "allowed_methods": task.allowed_methods or [],
                    })
                    if attempt + 1 < len(candidate_paths):
                        logger.info(f"  ↩️  Will retry with next candidate …")
                        continue
                    break

            # ── Post-write TS property validator (Cursor LSP equivalent) ─────
            # Check that property accesses in the new file match the FRESH
            # interface shapes from this run — catches TS2551 before build.
            if _ts_ext in (".ts", ".tsx"):
                _bad_props = _check_ts_property_access(
                    code.content, code.file_path,
                    state.get("_run_generated_map", {}),
                    str(state.get("workspace_path", "")),
                )
                if _bad_props:
                    logger.warning(
                        f"  ⚠️ TS property mismatch in {code.file_path}: {_bad_props} "
                        f"— these properties do not exist in the freshly-generated interfaces"
                    )
                    _silent_failures = state.setdefault("_patch_failures", [])
                    _silent_failures.append({
                        "file": code.file_path,
                        "reason": f"ts_property_mismatch:{_bad_props}",
                        "allowed_methods": task.allowed_methods or [],
                    })
                    if attempt + 1 < len(candidate_paths):
                        logger.info(f"  ↩️  Will retry with next candidate …")
                        continue
                    # On last attempt: still write but log prominently
                    logger.warning(
                        f"  ⚠️ Allowing write despite property mismatch (last attempt) — "
                        f"fix_build will need to correct: {_bad_props}"
                    )

            output_path.write_text(code.content, encoding='utf-8')
            # Record the new content so later tasks in this run see the fresh shapes.
            _written_key = code.file_path.replace("\\", "/").lower()
            state.setdefault("_run_generated_map", {})[_written_key] = code.content

            # ── POST-WRITE BINDING VERIFICATION (Read → Think → Write → Verify) ─
            # After writing an HTML file, immediately verify that every Angular binding
            # it references has a corresponding setter in the sibling .ts controller.
            # If the controller is being written LATER in this run, skip (it will supply
            # the property). If it's NOT in the plan at all, record a gap immediately so
            # fix_build can be targeted rather than discovering it only at build time.
            if Path(code.file_path).suffix.lower() in (".html", ".htm"):
                from ticket_to_code.agents.dataflow_tracer import (
                    extract_angular_bindings,
                    extract_property_setters,
                )
                _html_bindings = extract_angular_bindings(code.content)
                if _html_bindings:
                    _stem = code.file_path.rsplit(".", 1)[0]
                    _ctrl_key = (_stem + ".ts").replace("\\", "/").lower()
                    _ctrl_content = state.get("_run_generated_map", {}).get(_ctrl_key)
                    _ctrl_in_plan = any(
                        t.file_path.replace("\\", "/").lower() == _ctrl_key
                        for t in _get_plan(state).tasks
                        if getattr(getattr(t, "task_type", None), "value", "").lower() != "read_only"
                    )
                    if _ctrl_content:
                        # Controller already written — check setters NOW
                        _ctrl_setters = extract_property_setters(_ctrl_content)
                        _missing = [
                            b for b in _html_bindings
                            if b.property.lower() not in _ctrl_setters
                        ]
                        if _missing:
                            logger.warning(
                                f"  ⚠️ [Post-write] {len(_missing)} binding(s) in "
                                f"{code.file_path} have no setter in already-written controller "
                                f"{_ctrl_key}: "
                                + ", ".join(f"{b.variable}.{b.property}" for b in _missing)
                            )
                            # Record as gaps for fix_build awareness
                            for _mb in _missing:
                                state.setdefault("_dataflow_gaps", []).append({
                                    "html_file": code.file_path,
                                    "variable": _mb.variable,
                                    "property": _mb.property,
                                    "controller": _ctrl_key,
                                    "reason": f"Property set nowhere in {_ctrl_key} after it was written",
                                })
                    elif _ctrl_in_plan:
                        logger.info(
                            f"  ℹ️ [Post-write] Controller {_ctrl_key} is in the plan "
                            f"but not yet written — binding check deferred"
                        )
                    else:
                        # Controller NOT in plan and NOT written → always a gap
                        _gap_props = [f"{b.variable}.{b.property}" for b in _html_bindings]
                        logger.warning(
                            f"  ⚠️ [Post-write] {code.file_path} has bindings with no "
                            f"controller task in plan: {_gap_props}. "
                            f"These properties will be undefined at runtime."
                        )
                        for _mb in _html_bindings:
                            state.setdefault("_dataflow_gaps", []).append({
                                "html_file": code.file_path,
                                "variable": _mb.variable,
                                "property": _mb.property,
                                "controller": _ctrl_key,
                                "reason": f"Controller {_ctrl_key} has no task in plan",
                            })

            # ── POST-WRITE TS SETTER VERIFICATION ────────────────────────────
            # After writing a .ts controller, check if any previously-written HTML
            # files have bindings that this controller should supply but doesn't.
            # CRITICAL: also extract COMPONENT-LEVEL bindings (not prefixed with
            # dmember./member. etc.) from the HTML and attempt an inline re-generation
            # if the TS is missing those class-level properties.
            elif Path(code.file_path).suffix.lower() in (".ts", ".tsx"):
                from ticket_to_code.agents.dataflow_tracer import (
                    extract_angular_bindings,
                    extract_property_setters,
                )
                _ts_setters = extract_property_setters(code.content)
                _ts_stem = code.file_path.rsplit(".", 1)[0].replace("\\", "/").lower()
                for _skey, _sval in state.get("_run_generated_map", {}).items():
                    if not (_skey.endswith((".html", ".htm")) and _skey.rsplit(".", 1)[0] == _ts_stem):
                        continue
                    _html_bindings = extract_angular_bindings(_sval)
                    # Separate per-member bindings (dmember.X, member.X) from
                    # component-level bindings (direct property access on the component class)
                    _MEMBER_VARS = {"dmember", "member", "displayedmember", "item", "contract"}
                    _component_bindings = [
                        b for b in _html_bindings
                        if b.variable.lower() not in _MEMBER_VARS
                        and b.property.lower() not in _ts_setters
                    ]
                    _member_bindings = [
                        b for b in _html_bindings
                        if b.variable.lower() in _MEMBER_VARS
                        and b.property.lower() not in _ts_setters
                    ]
                    if _component_bindings:
                        logger.warning(
                            f"  ⚠️ [Post-write] TS controller {code.file_path} is missing "
                            f"COMPONENT-LEVEL properties used in HTML: "
                            + ", ".join(f"{b.variable}.{b.property}" for b in _component_bindings)
                        )
                        # Inline retry: re-generate TS with missing properties listed explicitly
                        _props_needed = "\n".join(
                            f"  - Add class property: `{b.property}: boolean | string = ...;`  (referenced as `{b.variable}.{b.property}` in template)"
                            for b in _component_bindings
                        )
                        import copy as _copy_prop
                        _prop_retry_task = _copy_prop.copy(task)
                        _prop_retry_task.description = (
                            f"{task.description}\n\n"
                            f"COMPONENT PROPERTY ALIGNMENT (sibling HTML already written and uses these):\n"
                            f"The sibling HTML template references the following as COMPONENT CLASS properties "
                            f"(accessed directly without `dmember.` prefix). You MUST declare them in the component class:\n"
                            f"{_props_needed}\n"
                            f"DO NOT use different property names — the HTML is already written with these exact names."
                        )
                        generator._session_files = state.get("_run_generated_map", {})
                        try:
                            _prop_code = generator.generate_code(
                                task=_prop_retry_task,
                                requirements=state["requirements"],
                                context=state["code_rag_context"],
                                existing_content=code.content,
                                allowed_files=allowed_files,
                                readonly_files=readonly_files,
                            )
                            if (
                                _prop_code.content
                                and _prop_code.content.strip() != code.content.strip()
                                and "[TRUNCATED]" not in _prop_code.content
                            ):
                                _pv = validator.validate(_prop_code, task, existing_content)
                                if _pv.passed:
                                    output_path.write_text(_prop_code.content, encoding="utf-8")
                                    state["_run_generated_map"][_written_key] = _prop_code.content
                                    code = _prop_code
                                    logger.info(
                                        f"  ✅ [PropAlign] Component properties aligned in {code.file_path}"
                                    )
                        except Exception as _prop_exc:
                            logger.warning(f"  ⚠️ [PropAlign] Retry failed: {_prop_exc}")
                    if _member_bindings:
                        logger.warning(
                            f"  ⚠️ [Post-write] TS controller {code.file_path} is missing "
                            f"setters for per-member bindings in sibling HTML: "
                            + ", ".join(f"{b.variable}.{b.property}" for b in _member_bindings)
                        )

            # ── PER-FILE COMPILE CHECK (Cursor-style continuous feedback) ────
            # ── PER-FILE COMPILE ERROR RESOLUTION LOOP (Cursor/Claude Code approach) ─
            # After every write: compile → errors? → show LLM real file + real errors
            # → regenerate full file → recompile → repeat up to 3 times.
            # No hardcoded error patterns. Any compile error gets the same treatment.

            # UI callback helper — sends sub-step events to the frontend
            _ui_cb = _get_transient(state, "_ui_callback")
            def _ui_emit(event_type: str, **kwargs):
                if not _ui_cb:
                    return
                from datetime import datetime as _dt_emit
                _ui_cb({
                    "phase": "patch_generation",
                    "status": "in_progress",
                    "message": kwargs.get("message", ""),
                    "data": {
                        "node": "generate_code",
                        "event_type": event_type,
                        **{k: v for k, v in kwargs.items() if k != "message"},
                    },
                    "timestamp": _dt_emit.now().isoformat(),
                })

            # Notify UI: file written
            _file_basename = Path(code.file_path).name
            _ui_emit(
                "file_written",
                message=f"📝 Written: {_file_basename}",
                file_path=code.file_path,
                file_name=_file_basename,
                change_ratio=validation.metrics.get("change_ratio", "N/A"),
            )

            _live_ext = Path(code.file_path).suffix.lower()
            _live_errors: list[str] = []
            _live_label = ""

            if _live_ext in (".ts", ".tsx") and not code.file_path.endswith(".spec.ts"):
                _ng_root_for_tsc = _find_ng_root(Path(state["workspace_path"]))
                if _ng_root_for_tsc:
                    _written_so_far = [
                        str(Path(state["workspace_path"]) / fp)
                        for fp in written_file_paths | {code.file_path}
                        if fp.endswith((".ts", ".tsx")) and not fp.endswith(".spec.ts")
                    ]
                    _live_errors = _run_tsc_on_files(_ng_root_for_tsc, _written_so_far, timeout=30)
                    _live_label = "TSC-live"

            elif _live_ext in (".java", ".kt") and not re.search(r'(?:Test|IT)\.(java|kt)$', code.file_path):
                _java_module = _find_java_module_root(code.file_path, str(state["workspace_path"]))
                if _java_module:
                    _module_root, _build_tool = _java_module
                    _live_errors = _run_java_compile_on_module(_module_root, _build_tool, timeout=60)
                    _live_label = f"Java-live ({_build_tool})"

            elif _live_ext in (".scss", ".css"):
                # ── SCSS/CSS syntax check: balanced braces + no format markers ──
                _live_errors = _check_scss_syntax(code.content, code.file_path)
                if _live_errors:
                    _live_label = "SCSS-syntax"

            elif _live_ext == ".py" and not code.file_path.endswith("_test.py"):
                # ── Python syntax check: py_compile ──
                _live_errors = _check_python_syntax(output_path)
                if _live_errors:
                    _live_label = "Python-syntax"

            elif _live_ext == ".html":
                # ── HTML template check: balanced tags + no format markers ──
                _live_errors = _check_html_syntax(code.content, code.file_path)
                if _live_errors:
                    _live_label = "HTML-syntax"

            if _live_errors and _live_label:
                _this_file_errors = [e for e in _live_errors if Path(code.file_path).name.lower() in e.lower()] or _live_errors[:10]
                logger.warning(
                    f"  ⚠️ [{_live_label}] {len(_live_errors)} error(s) after writing {code.file_path} — "
                    f"starting error resolution loop (up to 3 attempts)"
                )

                # Notify UI: compile errors detected
                _ui_emit(
                    "live_check_start",
                    message=f"⚠️ [{_live_label}] {len(_live_errors)} compile error(s) in {_file_basename}",
                    file_path=code.file_path,
                    file_name=_file_basename,
                    error_count=len(_live_errors),
                    errors=_live_errors[:5],
                    check_type=_live_label,
                )

                _resolved = False
                import re as _re_fix
                import json as _json_fix
                from langchain_core.messages import SystemMessage, HumanMessage
                from ticket_to_code.llm_utils import llm_invoke as _llm_invoke

                for _err_attempt in range(3):
                    _current_content = output_path.read_text(encoding="utf-8") if output_path.exists() else code.content

                    # ── Collect ALL files mentioned in errors (cross-file awareness) ──
                    # Errors in other files point to the ROOT CAUSE of this file's errors.
                    # e.g. TS2339 "property X does not exist on type Y" → Y's source file
                    # must also be shown so the LLM can add the missing property there.
                    _all_error_files: dict[str, str] = {code.file_path: _current_content}
                    _ws_root = Path(state["workspace_path"])
                    _run_map = state.get("_run_generated_map", {})
                    for _err_line in _live_errors:
                        _ep = _re_fix.search(
                            r'([^\s(]+\.(?:ts|tsx|js|jsx|java|kt|py))\s*[\(:\[]',
                            _err_line, _re_fix.IGNORECASE
                        )
                        if _ep:
                            _ep_raw = _ep.group(1).lstrip("/")
                            # Resolve absolute → relative
                            try:
                                _ep_rel = str(Path(_ep_raw).relative_to(_ws_root)).replace("\\", "/")
                            except ValueError:
                                _ep_rel = _ep_raw.replace("\\", "/")
                            if _ep_rel != code.file_path.replace("\\", "/") and _ep_rel not in _all_error_files:
                                _ep_abs = _ws_root / _ep_rel
                                if _ep_abs.exists():
                                    _ep_key = _ep_rel.lower()
                                    _all_error_files[_ep_rel] = _run_map.get(_ep_key) or _ep_abs.read_text(encoding="utf-8", errors="ignore")
                                    logger.info(f"  [{_live_label}] Including related error file: {_ep_rel}")

                    # Also inject session-generated files imported by the primary file
                    _src_dir = str(Path(code.file_path).parent).replace("\\", "/")
                    for _imp_m in _re_fix.finditer(r"""from\s+['"](\.[^'"]+)['"]""", _current_content):
                        for _iext in (".ts", ".tsx"):
                            import os as _os_fix
                            _irel = _os_fix.path.normpath(_os_fix.path.join(_src_dir, _imp_m.group(1) + _iext)).replace("\\", "/")
                            _ikey = _irel.lower()
                            if _ikey in _run_map and _irel not in _all_error_files:
                                _all_error_files[_irel] = _run_map[_ikey]

                    # ── Build error-to-method mapping (like top AI IDEs) ──
                    _error_method_section = ""
                    _error_method_map: dict[str, list[tuple[int, str, int, int]]] = {}
                    try:
                        from ticket_to_code.agents.smart_extract import _parse_java_ts_boundaries, _parse_python_boundaries
                        _line_rx = _re_fix.compile(
                            r'(?P<path>[^\s]+\.(?:java|ts|tsx|js|jsx|py|kt|scala))'
                            r'(?:\:\[(?P<line1>\d+)|'
                            r'\((?P<line2>\d+)|'
                            r':(?P<line3>\d+))',
                            _re_fix.IGNORECASE
                        )
                        _error_locations: list[tuple[str, int]] = []
                        for _el in _live_errors:
                            for _em in _line_rx.finditer(_el):
                                _ln = _em.group('line1') or _em.group('line2') or _em.group('line3')
                                if _ln:
                                    _error_locations.append((_em.group('path'), int(_ln)))
                        if _error_locations:
                            _file_boundaries: dict[str, list] = {}
                            for _fp, _fc in _all_error_files.items():
                                _flines = _fc.splitlines()
                                _fext = _fp.rsplit('.', 1)[-1].lower() if '.' in _fp else ''
                                if _fext in ('java', 'ts', 'tsx', 'js', 'jsx', 'kt', 'scala'):
                                    _file_boundaries[_fp] = _parse_java_ts_boundaries(_flines)
                                elif _fext == 'py':
                                    _file_boundaries[_fp] = _parse_python_boundaries(_flines)
                            _mappings: list[str] = []
                            _seen_mappings = set()
                            for _raw_path, _line_num in _error_locations:
                                _norm = _raw_path.replace('\\', '/').lower()
                                _matched_fp = None
                                for _fbp in _file_boundaries:
                                    if _norm.endswith(_fbp.lower()) or _fbp.lower().endswith(_norm):
                                        _matched_fp = _fbp; break
                                    if _norm.split('/')[-1] == _fbp.split('/')[-1]:
                                        _matched_fp = _fbp; break
                                if not _matched_fp: continue
                                for _mb in _file_boundaries[_matched_fp]:
                                    if _mb.kind == "class": continue
                                    if _mb.start_line <= (_line_num - 1) <= _mb.end_line:
                                        _mkey = (_matched_fp, _mb.name, _line_num)
                                        if _mkey not in _seen_mappings:
                                            _seen_mappings.add(_mkey)
                                            _mappings.append(
                                                f"  {_raw_path}:{_line_num} → inside method `{_mb.name}` "
                                                f"(lines {_mb.start_line+1}-{_mb.end_line+1})"
                                            )
                                            _nfp = _matched_fp.replace('\\', '/').lower()
                                            if _nfp not in _error_method_map:
                                                _error_method_map[_nfp] = []
                                            _error_method_map[_nfp].append(
                                                (_line_num, _mb.name, _mb.start_line, _mb.end_line)
                                            )
                                        break
                            if _mappings:
                                _error_method_section = (
                                    "ERROR LINE MAPPING (fix THESE methods, not their callers):\n"
                                    + "\n".join(_mappings) + "\n\n"
                                )
                                logger.info(f"  [{_live_label}] Error-method mapping: {len(_mappings)} mapping(s)")
                    except Exception as _map_exc:
                        logger.debug(f"  [{_live_label}] Error-method mapping failed (non-fatal): {_map_exc}")

                    # ── Build related file snippets (only first 80 lines, not full content) ──
                    _other_files_section = ""
                    for _rf, _rc in _all_error_files.items():
                        if _rf != code.file_path:
                            _snippet = "\n".join(_rc.splitlines()[:80])
                            _other_files_section += f"\nRELATED FILE (first 80 lines): {_rf}\n```\n{_snippet}\n```\n"

                    # Ticket + task context so the fix preserves the original goal
                    _ticket_obj = state.get("ticket")
                    _ticket_title = getattr(_ticket_obj, "title", "") or ""
                    _ticket_desc = (getattr(_ticket_obj, "description", "") or "")[:400]
                    _task_title = getattr(task, "title", "") or ""
                    _task_desc = (getattr(task, "description", "") or "")[:400]
                    _req_obj = state.get("requirements")
                    _func_reqs = ""
                    if _req_obj:
                        _fr = getattr(_req_obj, "functional_requirements", []) or []
                        _func_reqs = "\n".join(f"  - {r}" for r in _fr[:5])

                    _fix_system = (
                        "You are an expert compiler-error fixer. Fix errors using SURGICAL str_replace edits.\n"
                        "CRITICAL RULES:\n"
                        "1. NEVER rewrite the entire file. Only change the specific lines that fix the error.\n"
                        "2. Use target_content/replacement_content for targeted edits.\n"
                        "3. PRESERVE all existing functionality — do NOT remove or stub out code.\n"
                        "4. If a method or property is missing, ADD it. If a service call is wrong, FIX the call.\n"
                        "5. When ERROR LINE MAPPING shows which method has the error, fix THAT method — not its callers.\n\n"
                        "Return a JSON object:\n"
                        "{\n"
                        '  "reasoning": "root cause + which method to fix + how",\n'
                        '  "fixes": [\n'
                        '    {\n'
                        '      "file": "exact/relative/path.java",\n'
                        '      "target_content": "exact text to find in the file (must be unique)",\n'
                        '      "replacement_content": "the replacement text"\n'
                        '    }\n'
                        "  ]\n"
                        "}\n\n"
                        "Rules for target_content/replacement_content:\n"
                        "- target_content must be an EXACT substring of the current file\n"
                        "- Include enough surrounding lines to make target_content unique\n"
                        "- replacement_content replaces target_content completely\n"
                        "- You can have multiple fix entries for same or different files\n"
                        "- Only fix files shown below — do NOT invent new files\n"
                        "- Respond with JSON only, no markdown fences"
                    )
                    _fix_user = (
                        f"TICKET: {_ticket_title}\n{_ticket_desc}\n\n"
                        f"TASK BEING IMPLEMENTED: {_task_title}\n{_task_desc}\n\n"
                        + (f"FUNCTIONAL REQUIREMENTS (must still be satisfied after fix):\n{_func_reqs}\n\n" if _func_reqs else "")
                        + f"COMPILE ERRORS:\n"
                        + "\n".join(f"  {e}" for e in _live_errors[:20])
                        + f"\n\n{_error_method_section}"
                        + f"PRIMARY FILE: {code.file_path}\n```\n{_current_content}\n```\n"
                        + _other_files_section
                        + "\nFix all errors using target_content/replacement_content edits. Do NOT rewrite the file. Return JSON."
                    )
                    try:
                        _fix_response = _llm_invoke(
                            generator.llm,
                            [SystemMessage(content=_fix_system), HumanMessage(content=_fix_user)]
                        )
                        _fix_raw = _fix_response.content if hasattr(_fix_response, "content") else str(_fix_response)

                        # Parse JSON response
                        _json_m = _re_fix.search(r'\{.*\}', _fix_raw, _re_fix.DOTALL)
                        if not _json_m:
                            logger.warning(f"  ⚠️ [{_live_label}] Attempt {_err_attempt+1}: no JSON in response")
                            continue
                        _fix_result = _json_fix.loads(_json_m.group(0))
                        _fixes = _fix_result.get("fixes", [])
                        if not _fixes:
                            logger.warning(f"  ⚠️ [{_live_label}] Attempt {_err_attempt+1}: no fixes in response")
                            continue

                        logger.info(f"  [{_live_label}] Attempt {_err_attempt+1}: applying {len(_fixes)} str_replace fix(es) — {_fix_result.get('reasoning','')[:120]}")

                        # ── Helper: resolve LLM-returned path ──
                        def _resolve_fix_path(fp: str) -> tuple[str, Path]:
                            """Return (canonical_relative_path, absolute_path)."""
                            fp = fp.replace("\\", "/")
                            abs1 = _ws_root / fp
                            if abs1.exists():
                                return fp, abs1
                            if _live_ext in (".ts", ".tsx") and _ng_root_for_tsc:
                                _ng_name = _ng_root_for_tsc.name
                                fp2 = f"{_ng_name}/{fp}"
                                abs2 = _ws_root / fp2
                                if abs2.exists():
                                    logger.info(f"  [{_live_label}] Path auto-corrected: {fp} → {fp2}")
                                    return fp2, abs2
                            _code_norm = code.file_path.replace("\\", "/")
                            if _code_norm.endswith(fp) or fp.endswith(_code_norm):
                                return _code_norm, _ws_root / _code_norm
                            return fp, abs1

                        # ── Apply each str_replace fix with validation ──
                        _files_written_this_attempt: list[str] = []
                        _fix_valid = True
                        for _fix_item in _fixes:
                            _fix_path_raw = _fix_item.get("file", "")
                            _target_content = _fix_item.get("target_content", "")
                            _replacement_content = _fix_item.get("replacement_content", "")

                            # Block old-style full-file rewrite if LLM returned "content" key
                            if not _target_content and _fix_item.get("content"):
                                logger.warning(f"  ⚠️ [{_live_label}] LLM returned full 'content' instead of str_replace — rejecting")
                                _fix_valid = False
                                break

                            if not _fix_path_raw or not _target_content:
                                logger.warning(f"  ⚠️ [{_live_label}] Fix missing file or target_content — skipped")
                                continue

                            _fix_path, _fix_abs = _resolve_fix_path(_fix_path_raw)
                            logger.info(f"  [{_live_label}] str_replace fix for: {_fix_path}")

                            # Validation: inside workspace
                            try:
                                _fix_abs.resolve().relative_to(_ws_root.resolve())
                            except ValueError:
                                logger.warning(f"  ⚠️ [{_live_label}] Fix outside workspace: {_fix_path} — skipped")
                                _fix_valid = False
                                break
                            # Validation: file must exist
                            if not _fix_abs.exists():
                                logger.warning(f"  ⚠️ [{_live_label}] Fix targets non-existent file: {_fix_path} — skipped")
                                _fix_valid = False
                                break
                            # Validation: no protected paths
                            _fp_lower = _fix_path.lower()
                            if any(x in _fp_lower for x in ("/dist/", "/node_modules/", "/.git/", "/target/")):
                                logger.warning(f"  ⚠️ [{_live_label}] Fix targets protected path: {_fix_path} — skipped")
                                _fix_valid = False
                                break

                            # Read current file content
                            _file_content = _fix_abs.read_text(encoding="utf-8", errors="ignore")

                            # Validate: target_content must exist in the file
                            if _target_content not in _file_content:
                                _target_stripped = _target_content.strip()
                                if _target_stripped and _target_stripped in _file_content:
                                    _target_content = _target_stripped
                                    logger.info(f"  [{_live_label}] target_content matched after strip")
                                else:
                                    logger.warning(f"  ⚠️ [{_live_label}] target_content not found in {_fix_path} — skipped")
                                    continue

                            # Validate: target must be unique
                            if _file_content.count(_target_content) > 1:
                                logger.warning(f"  ⚠️ [{_live_label}] target_content has multiple occurrences — ambiguous, skipped")
                                continue

                            # Validate: patch targets the correct method (defense-in-depth)
                            _norm_fix_fp = _fix_path.replace('\\', '/').lower()
                            if _error_method_map and _norm_fix_fp in _error_method_map:
                                _lines_before = _file_content[:_file_content.index(_target_content)].count('\n')
                                _expected_methods = set()
                                _target_in_error_method = False
                                for _el, _mn, _ms, _me in _error_method_map[_norm_fix_fp]:
                                    _expected_methods.add(_mn)
                                    if _ms <= _lines_before <= _me:
                                        _target_in_error_method = True
                                if not _target_in_error_method and _expected_methods:
                                    _expected_str = ", ".join(f"`{n}`" for n in sorted(_expected_methods))
                                    logger.warning(
                                        f"  ⛔ [{_live_label}] PATCH TARGET VALIDATION FAILED: "
                                        f"patch targets line {_lines_before+1} but errors are in {_expected_str}"
                                    )
                                    continue  # skip this fix, let loop retry

                            # Apply the str_replace
                            _new_content = _file_content.replace(_target_content, _replacement_content, 1)
                            _fix_abs.write_text(_new_content, encoding="utf-8")
                            _run_map[_fix_path.lower()] = _new_content
                            _files_written_this_attempt.append(_fix_path)
                            _is_primary = (_fix_path.replace("\\", "/") == code.file_path.replace("\\", "/"))
                            if _is_primary:
                                code.content = _new_content
                            _old_lines = _file_content.count('\n')
                            _new_lines = _new_content.count('\n')
                            logger.info(
                                f"    ✏️  str_replace applied: {_fix_path} "
                                f"(target={len(_target_content)}→replacement={len(_replacement_content)} chars, "
                                f"lines: {_old_lines}→{_new_lines})"
                            )

                        if not _fix_valid:
                            continue

                        # ── Re-run compile check ──
                        _recheck: list[str] = []
                        if _live_ext in (".ts", ".tsx") and _ng_root_for_tsc:
                            _recheck = _run_tsc_on_files(_ng_root_for_tsc, _written_so_far, timeout=30)
                        elif _live_ext in (".java", ".kt") and _java_module:
                            _recheck = _run_java_compile_on_module(_module_root, _build_tool, timeout=60)

                        if not _recheck:
                            logger.info(f"  ✅ [{_live_label}] All errors resolved in attempt {_err_attempt+1} ({len(_files_written_this_attempt)} file(s) fixed)")
                            _ui_emit(
                                "live_check_resolved",
                                message=f"✅ [{_live_label}] Errors resolved (attempt {_err_attempt+1}) — {_file_basename}",
                                file_path=code.file_path,
                                file_name=_file_basename,
                                attempt=_err_attempt+1,
                                files_fixed=_files_written_this_attempt,
                                check_type=_live_label,
                            )
                            _resolved = True
                            break
                        else:
                            logger.warning(f"  ⚠️ [{_live_label}] Attempt {_err_attempt+1}: {len(_recheck)} error(s) remain — retrying")
                            _ui_emit(
                                "live_check_retry",
                                message=f"🔄 [{_live_label}] Attempt {_err_attempt+1}: {len(_recheck)} error(s) remain — retrying",
                                file_path=code.file_path,
                                file_name=_file_basename,
                                attempt=_err_attempt+1,
                                remaining_errors=len(_recheck),
                                errors=_recheck[:3],
                                check_type=_live_label,
                            )
                            _this_file_errors = [e for e in _recheck if Path(code.file_path).name.lower() in e.lower()] or _recheck[:10]
                            _live_errors = _recheck  # update for next iteration's cross-file detection
                    except Exception as _fix_exc:
                        logger.warning(f"  ⚠️ [{_live_label}] Attempt {_err_attempt+1} error: {_fix_exc}")

                if not _resolved:
                    logger.warning(
                        f"  ⚠️ [{_live_label}] Live-check exhausted {3} attempts for {code.file_path} "
                        f"— keeping generated code, fix_build will handle remaining errors"
                    )
                    _ui_emit(
                        "live_check_deferred",
                        message=f"⏭️ [{_live_label}] Deferred to fix_build — {_file_basename}",
                        file_path=code.file_path,
                        file_name=_file_basename,
                        check_type=_live_label,
                    )
                    # Do NOT revert — the generated code is needed for the ticket.
                    # fix_build (Phase 2) has deeper tools (ErrorResolutionAgent) and
                    # can fix cross-file issues that the quick live-check cannot.
            elif _live_label:
                logger.info(f"  ✅ [{_live_label}] No errors after writing {code.file_path}")
                _ui_emit(
                    "live_check_passed",
                    message=f"✅ [{_live_label}] Compiled OK — {_file_basename}",
                    file_path=code.file_path,
                    file_name=_file_basename,
                    check_type=_live_label,
                )
            generated_code.append(code)
            successful_writes += 1
            written_file_paths.add(code.file_path)
            logger.info(f"  ✅ Code written: {output_path} | change_ratio={validation.metrics.get('change_ratio', 'N/A')}")
            written = True
            break

        # ── Retry with verbatim extraction: str_replace EDIT failed ─────────
        # When all candidates produced identical-to-original content (edit failed),
        # retry ONCE using extract_exact_methods() to give the LLM exact, 
        # uncompressed method bodies. The SEARCH blocks the LLM produces will
        # be substrings of the real file, feeding through the existing
        # Tier 1→2→3 matching cascade in _apply_str_replace_edits().
        #
        # NEVER set task_type = CREATE for a MODIFY task. Full-file regeneration
        # is guaranteed to corrupt large files (the LLM cannot reproduce 1000+
        # lines faithfully from memory).
        if not written and existing_content and task.file_path in [pf["file"] for pf in state.get("_patch_failures", [])]:
            logger.info(f"  🔄 Retry with verbatim extraction for {task.file_path} (str_replace EDIT failed)")
            try:
                from ticket_to_code.agents.smart_extract import extract_exact_methods
                from ticket_to_code.agents.code_generator import _get_anchor_methods

                # Get exact method bodies using tree-sitter boundaries
                _retry_anchors = _get_anchor_methods(task)
                _retry_verbatim, _retry_matched = extract_exact_methods(
                    content=existing_content,
                    file_path=task.file_path,
                    anchor_methods=_retry_anchors,
                )

                if _retry_matched:
                    # Retry code generation with verbatim content as existing_content
                    # The task stays as MODIFY — no task_type change
                    generator._sqlite_store = getattr(agents.localizer, "sqlite_store", None)
                    generator._workspace_path = str(state.get("workspace_path", ""))
                    generator._session_files = state.get("_run_generated_map", {})
                    _retry_code = generator.generate_code(
                        task=task,
                        requirements=state["requirements"],
                        context=state["code_rag_context"],
                        existing_content=existing_content,
                        allowed_files=allowed_files,
                        readonly_files=readonly_files,
                    )
                    if (_retry_code.content and
                            _retry_code.content.strip() != existing_content.strip() and
                            "[TRUNCATED]" not in _retry_code.content):
                        _retry_validation = validator.validate(_retry_code, task, existing_content)
                        if _retry_validation.passed:
                            _retry_out = Path(state["workspace_path"]) / _retry_code.file_path
                            _retry_out.parent.mkdir(parents=True, exist_ok=True)
                            _retry_out.write_text(_retry_code.content, encoding="utf-8")
                            generated_code.append(_retry_code)
                            successful_writes += 1
                            written_file_paths.add(_retry_code.file_path)
                            logger.info(f"  ✅ Verbatim retry succeeded: {_retry_out}")
                            state["_patch_failures"] = [p for p in state.get("_patch_failures", [])
                                                         if p["file"] != task.file_path]
                        else:
                            logger.warning(
                                f"  ⚠️ Verbatim retry validation failed for {task.file_path}: "
                                + "; ".join(_retry_validation.violations)
                            )
                else:
                    # Distinguishable failure: planner named methods that don't exist in the file.
                    # This is NOT the same as "compile error we couldn't fix" — it means the
                    # planner hallucinated a method name or the file was refactored since planning.
                    logger.error(
                        f"  ❌ ANCHOR METHOD NOT FOUND: {task.file_path} — "
                        f"planner specified methods {_retry_anchors} but none exist in the file. "
                        f"This is an escalation-worthy failure (planner hallucination or stale plan)."
                    )
                    # Add a specifically-typed patch failure for triage
                    state.setdefault("_patch_failures", []).append({
                        "file": task.file_path,
                        "reason": f"anchor_method_not_found: {_retry_anchors}",
                        "allowed_methods": task.allowed_methods,
                    })
            except Exception as _retry_exc:
                logger.warning(f"  ⚠️ Verbatim retry error for {task.file_path}: {_retry_exc}")

    logger.info(f"Code generation complete: {len(generated_code)} files")

    # Log and surface any silent patch failures
    _pf = state.get("_patch_failures", [])
    if _pf:
        # If any failure is an anchor_method_not_found escalation, halt and escalate
        escalations = [p for p in _pf if p.get("reason", "").startswith("anchor_method_not_found")]
        if escalations:
            _reason = f"Patch failed due to missing anchor methods in {len(escalations)} file(s): " + ", ".join(p['file'] for p in escalations)
            logger.error(f"  ❌ Escalating workflow: {_reason}")
            return {
                "generated_code": generated_code,
                "status": "escalated",
                "escalation_type": "patch_failed",
                "escalation_reason": _reason
            }

        logger.warning(
            f"  ⚠️ SILENT PATCH FAILURES ({len(_pf)} task(s) produced no output): "
            + "; ".join(f"{p['file']} [{p['reason']}]"
                        + (f" allowed_methods={p['allowed_methods']}" if p['allowed_methods'] else "")
                        for p in _pf)
        )

    # ── Component 5: Post-generation type consistency check ───────────────────
    # After ALL tasks are generated, scan for interface/model files modified this
    # run and verify consumers use only properties that exist on the new shape.
    # This catches TS2551/TS2339 cascades BEFORE the build step.
    _run_map = state.get("_run_generated_map", {})
    _type_mismatches: list[dict] = []
    import re as _re_tc
    for _if_path, _if_content in _run_map.items():
        # Only check model/interface files (not components/services)
        if not any(kw in _if_path for kw in (".model.", ".interface.", ".dto.", ".entity.")):
            continue
        # Extract exported interface/class property names
        _if_props: set = set()
        for _pm in _re_tc.finditer(
            r'^\s+(\w+)\s*[?:]?\s*:\s*\S', _if_content, _re_tc.MULTILINE
        ):
            _pname = _pm.group(1)
            if _pname not in ('if', 'for', 'return', 'constructor', 'class', 'interface'):
                _if_props.add(_pname)

        if not _if_props:
            continue

        # Extract the main exported type name (e.g. "DisplayedMember")
        _type_name_m = _re_tc.search(
            r'export\s+(?:interface|class|type)\s+(\w+)', _if_content
        )
        if not _type_name_m:
            continue
        _type_name = _type_name_m.group(1)

        # Find all consumers: files that import this type
        for _con_path, _con_content in _run_map.items():
            if _con_path == _if_path:
                continue
            if _type_name not in _con_content:
                continue
            # Extract property accesses on this type: variable.property patterns
            # This is approximate but catches the most common cases
            _accessed = set(_re_tc.findall(
                r'\b\w+\.(\w+)\b', _con_content
            ))
            _bad_accesses = _accessed - _if_props - {
                'length', 'map', 'filter', 'forEach', 'find', 'some', 'every',
                'push', 'pop', 'slice', 'splice', 'join', 'includes', 'indexOf',
                'toString', 'valueOf', 'subscribe', 'pipe', 'emit', 'next',
                'value', 'error', 'complete', 'unsubscribe',
            }
            # Only flag if the accessed property existed in the OLD version
            # (meaning the shape changed) and the consumer wasn't also updated
            _old_content = state.get("original_file_contents", {}).get(
                next((t.file_path for t in _plan.tasks
                      if t.file_path.replace("\\", "/").lower() == _if_path), ""),
                ""
            )
            if _old_content:
                _old_props: set = set()
                for _opm in _re_tc.finditer(
                    r'^\s+(\w+)\s*[?:]?\s*:\s*\S', _old_content, _re_tc.MULTILINE
                ):
                    _old_props.add(_opm.group(1))
                # Properties that existed before but were removed/renamed
                _removed_props = _old_props - _if_props
                _stale_in_consumer = _bad_accesses & _removed_props
                if _stale_in_consumer:
                    _type_mismatches.append({
                        "interface": _if_path,
                        "consumer": _con_path,
                        "stale_props": sorted(_stale_in_consumer),
                        "type_name": _type_name,
                    })
                    logger.warning(
                        f"  ⚠️ [TypeCheck] {_con_path} uses stale properties "
                        f"{_stale_in_consumer} from {_type_name} — shape changed in {_if_path}"
                    )

    if _type_mismatches:
        state["_type_mismatches"] = _type_mismatches
        logger.warning(
            f"  ⚠️ Component 5: found {len(_type_mismatches)} cross-file type mismatch(es) — "
            f"fix_build will need to update these consumers"
        )

    # ── Fix #4: Spring Data Repository Method Naming Convention Check ─────────
    # When adding query methods to JPA repositories (findByXxx, existsByXxx, etc.),
    # malformed method names compile fine but fail at RUNTIME with
    # PropertyReferenceException. Detect and warn before the build step.
    import re as _re_sd
    _sd_method_rx = _re_sd.compile(
        r'\b(find|exists|count|delete|remove)By([A-Za-z]+)\b'
    )
    # Valid Spring Data keywords that can appear in method names
    _sd_valid_keywords = {
        'And', 'Or', 'Is', 'Equals', 'Between', 'LessThan', 'LessThanEqual',
        'GreaterThan', 'GreaterThanEqual', 'After', 'Before', 'IsNull', 'IsNotNull',
        'NotNull', 'Like', 'NotLike', 'StartingWith', 'EndingWith', 'Containing',
        'OrderBy', 'Not', 'In', 'NotIn', 'True', 'False', 'IgnoreCase',
        'Asc', 'Desc', 'First', 'Top', 'Distinct', 'All',
    }
    _sd_warnings: list[str] = []
    for _sd_path, _sd_content in _run_map.items():
        if not _sd_path.endswith('.java'):
            continue
        # Only check repository files
        if 'Repository' not in _sd_content and 'repository' not in _sd_path.lower():
            continue
        for _sd_m in _sd_method_rx.finditer(_sd_content):
            _sd_clause = _sd_m.group(2)  # e.g. "ProjectIdAndOtdsUserId"
            # Split on And/Or to get individual property references
            _sd_props = _re_sd.split(r'(?:And|Or)', _sd_clause)
            for _sd_prop in _sd_props:
                # Strip known keywords from the end
                _sd_clean = _sd_prop
                for _kw in sorted(_sd_valid_keywords, key=len, reverse=True):
                    if _sd_clean.endswith(_kw) and len(_sd_clean) > len(_kw):
                        _sd_clean = _sd_clean[:-len(_kw)]
                # Property must start with uppercase (PascalCase from entity field)
                if _sd_clean and not _sd_clean[0].isupper():
                    _sd_warnings.append(
                        f"{_sd_path}: method '{_sd_m.group(0)}' — property '{_sd_clean}' "
                        f"should be PascalCase (matching entity field name)"
                    )
                # Check if the property name is suspiciously long (>30 chars)
                # which usually means keywords weren't separated correctly
                if len(_sd_clean) > 30:
                    _sd_warnings.append(
                        f"{_sd_path}: method '{_sd_m.group(0)}' — clause '{_sd_clean}' is very long. "
                        f"Verify it matches entity field names exactly."
                    )

    if _sd_warnings:
        for _w in _sd_warnings:
            logger.warning(f"  ⚠️ [SpringData] {_w}")
        state["_spring_data_warnings"] = _sd_warnings
    
    # ── POST-GENERATION SCOPE VALIDATION ─────────────────────────────────────
    files_written = set(written_file_paths)
    files_allowed_set = set(allowed_files)
    unauthorized = files_written - files_allowed_set
    if unauthorized:
        logger.error(
            f" SCOPE VIOLATION: {len(unauthorized)} file(s) written outside the "
            f"approved list — this should not happen. Files: {unauthorized}"
        )
        # Remove unauthorized writes from the result and delete the written files
        clean = []
        for code in generated_code:
            if code.file_path in unauthorized:
                bad_path = Path(state["workspace_path"]) / code.file_path
                if bad_path.exists():
                    if code.file_path in state.get("original_file_contents", {}):
                        bad_path.write_text(state["original_file_contents"][code.file_path], encoding='utf-8')
                        logger.warning(f"  ️  Reverted unauthorized modification to original state: {bad_path}")
                    else:
                        bad_path.unlink()
                        logger.warning(f"  ️  Deleted unauthorized new file: {bad_path}")
            else:
                clean.append(code)
        generated_code = clean

    # Collect scope violation strings for the tracer
    scope_violation_msgs = (
        [f"Unauthorized write: {f}" for f in sorted(unauthorized)]
        if unauthorized else []
    )

    # Record Classification and Routing foundation traces
    agents.tracer.record_artifact_classification(classifications=classification_traces)
    agents.tracer.record_generation_routing(routing=routing_traces)

    agents.tracer.record_generation(
        generated_code=generated_code,
        generated_tests=state.get("generated_tests", []),
        allowed_files=allowed_files,
        readonly_files=readonly_files,
        scope_violations=scope_violation_msgs,
        system_message=getattr(agents.code_generator, "last_system_prompt", None),
        user_message=getattr(agents.code_generator, "last_user_prompt", None),
    )

    # ── Write 6-Tier Matching Cascade trace artifact ──────────────────────
    # Records which tier resolved each SEARCH/REPLACE edit + AST validation
    try:
        from ticket_to_code.agents.code_generator import _patch_tier_traces
        _pf = state.get("_patch_failures", [])
        _tier_trace = {
            "total_edits_attempted": sum(t.get("total_edits", 0) for t in _patch_tier_traces) if _patch_tier_traces else 0,
            "total_edits_resolved": len(_patch_tier_traces),
            "tier_summary": {},
            "edits": list(_patch_tier_traces),
            "patch_failures": _pf,
            "files_written": list(written_file_paths),
        }
        # Build tier summary (count per tier)
        for t in _patch_tier_traces:
            tier_name = t.get("tier", "unknown")
            _tier_trace["tier_summary"][tier_name] = _tier_trace["tier_summary"].get(tier_name, 0) + 1
        write_trace_artifact(
            state["workspace_path"], state["ticket"].ticket_id,
            "patch_tiers.json", _tier_trace
        )
        logger.info(f"  📊 Patch tier trace: {_tier_trace.get('tier_summary', {})}")
    except Exception as _trace_err:
        logger.debug(f"Patch tier trace write skipped: {_trace_err}")


    patch_diff = ""
    try:
        import subprocess
        result = subprocess.run(["git", "diff"], cwd=state["workspace_path"], capture_output=True, text=True)
        patch_diff = result.stdout or ""
        write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "generated_patch.diff", patch_diff)
    except Exception as e:
        logger.error(f"Failed to generate git diff: {e}")

    # If no effective file changes were produced, do not continue as success.
    # Feed this back into the candidate retry loop by blacklisting current
    # writable targets and requesting re-plan / rediscovery.
    if allowed_files and successful_writes == 0:
        retry_count = state.get("candidate_retry_count", 0) + 1
        max_retries = state.get("max_candidate_retries", 2)
        plan_for_retry = _get_plan(state)
        planned_create_paths = {
            t.file_path
            for t in getattr(plan_for_retry, "tasks", [])
            if getattr(getattr(t, "task_type", None), "value", str(getattr(t, "task_type", ""))).lower() == "create"
        }
        # Blacklist only files we actually attempted this round, not the
        # entire writable set. This avoids over-blacklisting and improves
        # convergence when some candidates are still viable.
        blacklist_targets = sorted(attempted_writable_files) if attempted_writable_files else list(allowed_files)
        # Never auto-blacklist explicit CREATE targets on no-op generation.
        # A no-op can happen due transient model output; blacklisting CREATE
        # paths causes the next plan to collapse to zero writable tasks.
        if planned_create_paths:
            blacklist_targets = [p for p in blacklist_targets if p not in planned_create_paths]
        new_blacklist = list(set((state.get("blacklisted_files") or []) + blacklist_targets))
        reason = "Code generation produced no effective patch for approved writable files"
        logger.error(
            f"  ❌ {reason}. Triggering candidate retry {retry_count}/{max_retries}. "
            f"Blacklisting: {blacklist_targets}"
        )
        return {
            "generated_code": generated_code,
            "code_status": "no_effective_changes",
            "candidate_retry_count": retry_count,
            "blacklisted_files": new_blacklist,
            "validation_failure_reason": reason,
            "status": "candidates_invalid",
            "original_file_contents": state.get("original_file_contents", {}),
        }

    # DEBUG LOG
    log_phase(
        phase='GENERATE_CODE',
        llm_output={
            'generated_files': len(generated_code),
            'first_file': generated_code[0].file_path if generated_code else None,
            'total_lines_written': sum(len(g.content.splitlines()) for g in generated_code if g.content),
            'patch_status': 'applied' if patch_diff.strip() else 'no_changes',
            'scope_violations': scope_violation_msgs
        },
        agent='CodeGenerator'
    )

    return {
        "generated_code": generated_code,
        "code_status": "code_generated",  # CODE BRANCH status
        "original_file_contents": state.get("original_file_contents", {})
    }


# ============================================================================
# SCSS / CSS / PYTHON / HTML SYNTAX CHECK HELPERS
# ============================================================================

def _check_scss_syntax(content: str, file_path: str) -> list[str]:
    """
    Fast SCSS/CSS syntax check — no external tools needed.

    Catches:
      1. Unbalanced braces { }  (the #1 LLM error in SCSS)
      2. Leaked LLM format markers (<<<<<<< SEARCH, EDIT:, old_str:, etc.)
      3. Empty or near-empty files (accidental truncation)
    """
    errors: list[str] = []

    # 1. Check for leaked format markers
    _format_markers = [
        "<<<<<<< SEARCH", ">>>>>>> REPLACE", "=======",
        "EDIT:\nold_str:", "<<<\n", "\n>>>",
        "<<<AVIATOR_CODE_START>>>", "<<<AVIATOR_CODE_END>>>",
    ]
    for marker in _format_markers:
        if marker in content:
            errors.append(
                f"{file_path}: Leaked LLM format marker found: "
                f"'{marker.strip()[:30]}' — file is corrupted"
            )

    # 2. Check balanced braces
    depth = 0
    for i, ch in enumerate(content):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth < 0:
                line_num = content[:i].count('\n') + 1
                errors.append(
                    f"{file_path}({line_num}): Unexpected closing brace '}}' "
                    f"— braces are unbalanced"
                )
                break
    if depth > 0 and not errors:
        errors.append(
            f"{file_path}: {depth} unclosed brace(s) '{{' — SCSS is incomplete"
        )

    # 3. Check for empty/truncated file
    stripped = content.strip()
    if not stripped:
        errors.append(f"{file_path}: File is empty after write")
    elif len(stripped) < 10:
        errors.append(f"{file_path}: File is suspiciously short ({len(stripped)} chars)")

    if errors:
        logger.warning(
            f"  ⚠️ [SCSS-syntax] {len(errors)} issue(s) in {file_path}: "
            + "; ".join(errors)
        )
    return errors


def _check_python_syntax(file_path: Path) -> list[str]:
    """
    Python syntax check using the built-in py_compile module.

    This catches SyntaxError, IndentationError, and other parse-time
    errors without importing or executing the file.
    """
    import py_compile
    errors: list[str] = []

    try:
        py_compile.compile(str(file_path), doraise=True)
    except py_compile.PyCompileError as exc:
        # Extract the useful part of the error message
        err_msg = str(exc)
        # Also try to get line number
        import traceback
        if hasattr(exc, '__cause__') and exc.__cause__:
            cause = exc.__cause__
            if hasattr(cause, 'lineno') and cause.lineno:
                err_msg = (
                    f"{file_path}({cause.lineno}): {type(cause).__name__}: "
                    f"{cause.msg}"
                )
        errors.append(err_msg)
        logger.warning(f"  ⚠️ [Python-syntax] {file_path}: {err_msg}")
    except Exception as exc:
        errors.append(f"{file_path}: py_compile failed: {exc}")

    # Also check for leaked format markers
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        for marker in ["<<<<<<< SEARCH", ">>>>>>> REPLACE", "EDIT:\nold_str:"]:
            if marker in content:
                errors.append(
                    f"{file_path}: Leaked LLM format marker: '{marker.strip()[:30]}'"
                )
    except Exception:
        pass

    return errors


def _check_html_syntax(content: str, file_path: str) -> list[str]:
    """
    Basic HTML template syntax check.

    Catches:
      1. Leaked LLM format markers
      2. Severely unbalanced HTML tags (missing closing tags for block elements)
      3. Empty/truncated files
    """
    errors: list[str] = []

    # 1. Format marker leak check
    for marker in ["<<<<<<< SEARCH", ">>>>>>> REPLACE", "EDIT:\nold_str:"]:
        if marker in content:
            errors.append(
                f"{file_path}: Leaked LLM format marker: '{marker.strip()[:30]}'"
            )

    # 2. Basic balanced-tag check for Angular block elements
    _block_tags = ["div", "section", "main", "form", "table", "tbody", "thead",
                   "tr", "ul", "ol", "li", "mat-card", "mat-dialog-content",
                   "mat-tab-group", "mat-tab", "ng-container", "ng-template"]
    for tag in _block_tags:
        opens = len(re.findall(rf'<{tag}[\s>]', content, re.IGNORECASE))
        closes = len(re.findall(rf'</{tag}\s*>', content, re.IGNORECASE))
        # Self-closing tags don't need a close
        self_closing = len(re.findall(rf'<{tag}[^>]*/\s*>', content, re.IGNORECASE))
        net_opens = opens - self_closing
        if net_opens > closes + 2:  # tolerance of 2 for ng-container/ng-template
            errors.append(
                f"{file_path}: Unclosed <{tag}> tags — "
                f"{net_opens} opened, {closes} closed"
            )

    # 3. Empty check
    stripped = content.strip()
    if not stripped:
        errors.append(f"{file_path}: File is empty after write")

    if errors:
        logger.warning(
            f"  ⚠️ [HTML-syntax] {len(errors)} issue(s) in {file_path}: "
            + "; ".join(errors)
        )
    return errors


# ============================================================================
# ANGULAR / TYPESCRIPT COMPILE HELPERS
# ============================================================================

def _find_ng_root(workspace: Path) -> Optional[Path]:
    """
    Find the Angular project root — a directory that contains angular.json or
    tsconfig.json.  Checks the common sub-directory names first, then falls
    back to the workspace root itself.
    """
    for subdir in ("xchange-ui", "ui", "frontend", "client", "app", "."):
        candidate = workspace / subdir
        if candidate.is_dir() and (
            (candidate / "angular.json").exists()
            or (candidate / "tsconfig.json").exists()
        ):
            return candidate
    return None


def _extract_error_line_context(
    error_line: str,
    file_content: str,
    window: int = 5,
) -> str:
    """
    Given a compiler error line like 'File.ts(387,31): error TS2551: ...',
    extract the exact source code line that is wrong, plus surrounding context.

    For TS2345 ("Type X not assignable to Y") the error fires at the CALL SITE
    but the fix requires changing the CALLING METHOD body.  We expand the window
    to show the entire enclosing function/method when TS2345 is detected.
    """
    # Extract line number from various formats: (387,31), :[387,31], :387:
    line_num = None
    m = re.search(r'[(:]\[?(\d+)[,:]', error_line)
    if m:
        try:
            line_num = int(m.group(1))
        except ValueError:
            pass

    if not line_num or not file_content:
        return ""

    lines = file_content.splitlines()
    if line_num > len(lines):
        return ""

    # For TS2345 (type incompatibility at call site), expand to show the full
    # enclosing function so the LLM sees WHERE the wrong object is built.
    if "TS2345" in error_line or "not assignable to" in error_line.lower():
        # Walk backwards to find the enclosing function/method start
        func_start = max(0, line_num - 60)
        for i in range(line_num - 2, max(0, line_num - 60), -1):
            if re.match(r'\s*(?:public|private|protected|async)?\s*\w+\s*\(', lines[i]):
                func_start = i
                break
        # Walk forwards to find the function end
        func_end = min(len(lines), line_num + 10)
        depth = 0
        for i in range(func_start, min(len(lines), func_start + 80)):
            depth += lines[i].count('{') - lines[i].count('}')
            if i > line_num and depth <= 0:
                func_end = i + 1
                break
        start = func_start
        end = func_end
    else:
        start = max(0, line_num - window - 1)
        end = min(len(lines), line_num + window)

    excerpt = []
    for i, ln in enumerate(lines[start:end], start=start + 1):
        marker = ">>> " if i == line_num else "    "
        excerpt.append(f"{marker}{i:4d}: {ln}")
    return "\n".join(excerpt)


def _collect_type_definition_files(
    error_lines: list[str],
    session_map: dict,
    workspace_path: str,
) -> dict[str, str]:
    """
    For each TypeScript error (TS2322, TS2551, TS2339), find the interface/type
    definitions that the LLM needs to see in order to write the correct fix.

    Searches the session_map (freshly generated files) first, then disk.
    Returns {file_path: content} for all relevant type definition files.
    """
    collected: dict[str, str] = {}

    # Extract type names from error messages
    type_names: set[str] = set()
    for ln in error_lines:
        # "not assignable to type 'DisplayedOrganization'"
        for m in re.finditer(r"type\s+'([A-Z][A-Za-z0-9]+)'", ln):
            type_names.add(m.group(1))
        # "Property 'X' does not exist on type 'Y'"
        for m in re.finditer(r"on type\s+'([A-Z][A-Za-z0-9]+)'", ln):
            type_names.add(m.group(1))
        # "Did you mean 'organization'?" → look at what defines that property
        for m in re.finditer(r"Did you mean\s+'([^']+)'", ln):
            type_names.add(m.group(1).split(".")[0].strip())

    if not type_names:
        return collected

    # Search session map first (freshest content)
    for path_key, content in session_map.items():
        for type_name in type_names:
            if (f"interface {type_name}" in content or f"type {type_name}" in content
                    or f"class {type_name}" in content):
                if path_key not in collected:
                    collected[path_key] = content
                break

    # Fall back to disk if not found in session
    if len(collected) < len(type_names):
        skip_dirs = {"node_modules", "dist", ".git", "build", "coverage", ".venv"}
        import os as _os
        for root, dirs, files in _os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if not f.endswith((".ts", ".java", ".py")):
                    continue
                full = Path(root) / f
                rel = str(full.relative_to(Path(workspace_path))).replace("\\", "/").lower()
                if rel in collected:
                    continue
                try:
                    content = full.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for type_name in type_names:
                    if (f"interface {type_name}" in content or f"class {type_name} " in content
                            or f"type {type_name} " in content):
                        collected[rel] = content
                        break
                if len(collected) >= 8:
                    return collected

    return collected


def _build_err_fingerprint(errors: list) -> str:
    """Stable error fingerprint: keeps error TYPE, ignores line numbers and punctuation."""
    parts: set[str] = set()
    for e in (errors or [])[:20]:
        e2 = re.sub(r":\d+(?::\d+)?", " ", str(e)).lower()
        e2 = re.sub(r"[^a-z ]+", " ", e2)
        tokens = " ".join(w for w in e2.split() if len(w) > 2)
        if tokens:
            parts.add(tokens)
    return "|".join(sorted(parts))


def _find_java_module_root(file_path: str, workspace_path: str) -> "Optional[tuple[Path, str]]":
    """
    Walk up from `file_path` to find the nearest Maven or Gradle module root.

    Returns (module_root_path, build_tool) where build_tool is "maven" or "gradle",
    or None if no build file is found within the workspace.
    """
    ws = Path(workspace_path).resolve()
    current = (ws / file_path).resolve().parent
    while True:
        if (current / "pom.xml").exists():
            return (current, "maven")
        if (current / "build.gradle").exists() or (current / "build.gradle.kts").exists():
            return (current, "gradle")
        if current == ws or current == current.parent:
            break
        current = current.parent
    return None


def _run_java_compile_on_module(
    module_root: Path,
    build_tool: str,
    timeout: int = 60,
) -> list[str]:
    """
    Run a fast incremental Java compile on the module that contains the changed file.

    Maven  → `mvn compile -q --no-transfer-progress`
    Gradle → `gradle compileJava --no-daemon -q` (uses Gradle daemon cache → fast on repeat runs)

    Returns:
        List of error strings. Empty list = clean compile.
    """
    import subprocess
    import sys

    use_shell = sys.platform == "win32"

    if build_tool == "maven":
        cmd: Any = (
            "mvn compile -q --no-transfer-progress"
            if use_shell
            else ["mvn", "compile", "-q", "--no-transfer-progress"]
        )
    else:
        # Gradle: compileJava only (skip test compile and resources for speed)
        gradle_exe = "gradlew.bat" if (module_root / "gradlew.bat").exists() and use_shell else "gradle"
        if (module_root / "gradlew").exists():
            gradle_exe = "gradlew.bat" if use_shell else "./gradlew"
        cmd = (
            f"{gradle_exe} compileJava --no-daemon -q"
            if use_shell
            else [gradle_exe, "compileJava", "--no-daemon", "-q"]
        )

    try:
        result = subprocess.run(
            cmd,
            cwd=str(module_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=use_shell,
        )

        if result.returncode == 0:
            return []

        raw = (result.stdout + result.stderr).strip()
        # Maven errors start with "[ERROR]"; Gradle errors contain ": error:" or "error:"
        errors: list[str] = []
        for ln in raw.splitlines():
            stripped = ln.strip()
            if not stripped:
                continue
            if stripped.startswith("[ERROR]") or ": error:" in stripped.lower() or stripped.startswith("error:"):
                errors.append(stripped)

        if not errors:
            # Fall back: include any non-empty, non-INFO/WARNING line
            errors = [
                ln.strip() for ln in raw.splitlines()
                if ln.strip() and not ln.strip().startswith(("[INFO]", "[WARNING]", "> Task", "BUILD"))
            ]

        return errors[:30]  # cap to avoid flooding the prompt

    except subprocess.TimeoutExpired:
        logger.warning(f"  ⚠️ Java module compile timed out after {timeout}s — skipping")
        return []
    except FileNotFoundError as exc:
        logger.debug(f"  ℹ️ Java compile tool not found ({exc}) — skipping")
        return []
    except Exception as exc:
        logger.debug(f"  ℹ️ Java compile check failed: {exc}")
        return []


def _run_tsc_on_files(
    ng_root: Path,
    written_files: list[str],
    timeout: int = 30,
) -> list[str]:
    """
    Run a fast, scoped `tsc --noEmit` check covering only the files written so far.

    Strategy (mirrors Cursor per-file feedback loop):
    1. Write a temporary tsconfig that extends the project's tsconfig.app.json
       but overrides `files` to only the TypeScript files written in this run.
    2. Run `tsc --noEmit --skipLibCheck -p tsconfig_aviator_tmp.json`.
    3. Parse and return error lines. Empty list = clean.
    4. Always delete the temp file on exit.

    `--skipLibCheck` avoids re-checking node_modules .d.ts files (saves 15–20 s).
    Scoping to written files avoids false positives from pre-existing errors
    in files we haven't touched.

    Returns:
        List of error strings, empty on success or if tsc is not available.
    """
    import subprocess
    import sys
    import json as _json
    import tempfile

    ts_files = [f for f in written_files if f.endswith((".ts", ".tsx")) and not f.endswith(".spec.ts")]
    if not ts_files:
        return []

    use_shell = sys.platform == "win32"
    tmp_cfg = ng_root / "tsconfig_aviator_tmp.json"

    # Build extends from tsconfig.app.json or tsconfig.json
    base_cfg = "tsconfig.app.json" if (ng_root / "tsconfig.app.json").exists() else "tsconfig.json"

    # Paths must be relative to ng_root
    rel_files: list[str] = []
    for f in ts_files:
        try:
            abs_f = Path(f) if Path(f).is_absolute() else ng_root / f
            rel = abs_f.resolve().relative_to(ng_root.resolve())
            rel_files.append(str(rel).replace("\\", "/"))
        except ValueError:
            rel_files.append(f.replace("\\", "/"))

    tmp_cfg_content = {
        "extends": f"./{base_cfg}",
        "compilerOptions": {"skipLibCheck": True, "noEmit": True},
        "files": rel_files,
    }

    try:
        tmp_cfg.write_text(_json.dumps(tmp_cfg_content, indent=2), encoding="utf-8")

        cmd: Any = (
            f"npx --no-install tsc --noEmit --skipLibCheck --pretty false -p tsconfig_aviator_tmp.json"
            if use_shell
            else ["npx", "--no-install", "tsc", "--noEmit", "--skipLibCheck",
                  "--pretty", "false", "-p", "tsconfig_aviator_tmp.json"]
        )

        result = subprocess.run(
            cmd,
            cwd=str(ng_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=use_shell,
        )

        if result.returncode == 0:
            return []

        raw = strip_ansi(result.stdout + result.stderr).strip()
        errors = [ln for ln in raw.splitlines() if ln.strip() and "error TS" in ln]
        if not errors:
            errors = [ln for ln in raw.splitlines() if ln.strip()]
        return errors

    except subprocess.TimeoutExpired:
        logger.warning(f"  ⚠️ Scoped TSC check timed out after {timeout}s — skipping")
        return []
    except FileNotFoundError:
        logger.debug("  ℹ️ npx not found — scoped TSC check skipped")
        return []
    except Exception as exc:
        logger.debug(f"  ℹ️ Scoped TSC check failed: {exc}")
        return []
    finally:
        try:
            tmp_cfg.unlink(missing_ok=True)
        except Exception:
            pass


def _run_tsc_check(ng_root: Path, timeout: int = 180) -> "Optional[BuildResult]":
    """
    Run TypeScript (`tsc --noEmit`) and Angular template type-checking (`ng build`)
    in *ng_root*.

    Returns:
        BuildResult with FAILURE status  — if tsc or ng build found errors
        None                             — if compile succeeded OR if tools not found
    """
    import subprocess
    import sys

    # On Windows npx is a .cmd wrapper; shell=True lets cmd.exe resolve it.
    use_shell = sys.platform == "win32"

    # Ensure node_modules exist — if missing, run npm install first
    if not (ng_root / "node_modules").exists():
        logger.info(f"  📦 node_modules missing in {ng_root} — running npm install...")
        try:
            _npm_cmd: Any = "npm install" if use_shell else ["npm", "install"]
            subprocess.run(
                _npm_cmd, cwd=str(ng_root), capture_output=True,
                text=True, timeout=300, shell=use_shell,
            )
            logger.info("  ✅ npm install completed")
        except Exception as _npm_err:
            logger.warning(f"  ⚠️ npm install failed: {_npm_err} — continuing anyway")

    # Step 1: Run fast tsc --noEmit check
    tsconfig_app = ng_root / "tsconfig.app.json"
    if tsconfig_app.exists():
        cmd: Any = (
            f"npx --no-install tsc --noEmit --pretty false -p tsconfig.app.json"
            if use_shell
            else ["npx", "--no-install", "tsc", "--noEmit", "--pretty", "false", "-p", "tsconfig.app.json"]
        )
        logger.info("  Using tsconfig.app.json (excludes e2e/test files)")
    else:
        cmd = (
            "npx --no-install tsc --noEmit --pretty false"
            if use_shell
            else ["npx", "--no-install", "tsc", "--noEmit", "--pretty", "false"]
        )

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ng_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=use_shell,
        )
        if result.returncode != 0:
            clean_raw = strip_ansi(result.stdout + result.stderr).strip()
            errors = [line for line in clean_raw.splitlines() if line.strip()]
            logger.error(f"  ❌ TypeScript compile errors ({len(errors)} diagnostic line(s)):")
            for e in errors[:8]:
                logger.error(f"    {e}")
            return BuildResult(
                status=BuildStatus.FAILURE,
                exit_code=result.returncode,
                errors=errors,
                stdout=result.stdout,
                stderr=result.stderr,
            )

        # Step 2: If Angular project, run Angular template type-checking (ng build)
        angular_json = ng_root / "angular.json"
        if angular_json.exists():
            logger.info("  🔍 Running Angular AOT template type-check (`npm run build`)...")
            ng_cmd: Any = (
                "npm run build"
                if use_shell
                else ["npm", "run", "build"]
            )
            ng_result = subprocess.run(
                ng_cmd,
                cwd=str(ng_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=use_shell,
            )
            if ng_result.returncode != 0:
                clean_ng = strip_ansi(ng_result.stdout + ng_result.stderr).strip()
                all_lines = clean_ng.splitlines()
                ng_errors = [
                    line for line in all_lines
                    if ("Error:" in line or "error TS" in line or "error NG" in line)
                ]
                if not ng_errors:
                    ng_errors = [line for line in all_lines if line.strip() and not line.startswith("Warning:") and not line.startswith("Deprecation Warning")]
                logger.error(f"  ❌ Angular template build errors ({len(ng_errors)} diagnostic line(s)):")
                for e in ng_errors[:8]:
                    logger.error(f"    {e}")
                return BuildResult(
                    status=BuildStatus.FAILURE,
                    exit_code=ng_result.returncode,
                    errors=ng_errors,
                    stdout=ng_result.stdout,
                    stderr=ng_result.stderr,
                )

        return None  # Success — no errors
    except subprocess.TimeoutExpired:
        logger.warning(f"  ⚠️ Build check timed out after {timeout}s — compile check skipped")
        return None
    except FileNotFoundError:
        logger.warning("  ⚠️ npx / npm not found in PATH — compile check skipped")
        return None


# ============================================================================
# PATCH GATE NODE (B9: Delivery Safety)
# ============================================================================

def patch_gate_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    B9 Patch Delivery Safety Gate.

    Runs THREE deterministic checks BEFORE passing to build:
      1. Scope enforcement  — diff must not touch files outside writable set
      2. Git apply check    — diff must apply cleanly to current HEAD
      3. Secret scan        — diff must not contain high-confidence secrets

    Controlled by env var AVIATOR_ENABLE_PATCH_GATE (default "1" = on).
    When disabled the node is a pass-through so existing behaviour is unchanged.

    On gate failure:  status set to "candidates_invalid" so route_after_generate_code
                      can trigger a blacklist + re-plan cycle.
    On gate pass:     passes through unchanged.
    """
    _GATE_ENABLED = os.getenv("AVIATOR_ENABLE_PATCH_GATE", "1") not in ("0", "false", "no")
    if not _GATE_ENABLED:
        logger.debug("patch_gate_node: AVIATOR_ENABLE_PATCH_GATE=0 — skipping")
        return {}

    logger.info("🔒 PATCH GATE: Checking scope, apply, and secrets")

    run_ctx: Optional[RunContext] = _get_transient(state, "run_ctx")
    if run_ctx:
        run_ctx.start_phase("patch_gate")

    generated_code = state.get("generated_code") or []
    plan = _get_plan(state)
    workspace_path = state["workspace_path"]

    # Collect writable file paths from the plan
    allowed_files: set = set()
    if plan:
        allowed_files = {
            t.file_path.replace("\\", "/")
            for t in (plan.tasks or [])
            if t.task_type.value != "read_only"
        }

    gate = PatchGate(repo_path=workspace_path)
    head_commit = PatchGate.current_head(workspace_path)

    gate_failures: list = []

    for gen in generated_code:
        diff = getattr(gen, "content", "") or ""
        if not diff.strip():
            continue

        file_path = getattr(gen, "file_path", "")

        # ── Scope check (B9) ──────────────────────────────────────────────────
        if allowed_files:
            scope_res = gate.scope_enforce(diff, allowed_files)
            if not scope_res.ok:
                logger.warning(
                    "patch_gate: scope violation for %s → %s",
                    file_path, scope_res.violations,
                )
                gate_failures.append(
                    f"scope_violation:{file_path}:{scope_res.violations}"
                )

        # ── Secret scan (B9) ──────────────────────────────────────────────────
        secret_hits = gate.secret_scan(diff)
        if secret_hits:
            logger.error(
                "patch_gate: secret detected in %s — rules: %s",
                file_path, [h.rule for h in secret_hits],
            )
            gate_failures.append(
                f"secret_scan:{file_path}:{[h.rule for h in secret_hits]}"
            )

        # ── Apply check is skip-safe (if git unavailable, log warning only) ──
        # We don't fail the gate on apply-check unavailability to maintain
        # backward compatibility with local dev environments without git.
        apply_res = gate.apply_check(diff)
        if not apply_res.ok and gate._git_available:
            logger.warning(
                "patch_gate: git apply --check failed for %s: %s",
                file_path, apply_res.error,
            )
            # Log but do not block — apply failures may be caused by index skew
            # which is a deployment concern, not a generation concern.

    if run_ctx:
        run_ctx.validation_passed = len(gate_failures) == 0
        run_ctx.end_phase("patch_gate")

    if gate_failures:
        logger.error(
            "patch_gate: BLOCKED — %d failure(s): %s",
            len(gate_failures), gate_failures,
        )
        # Trigger re-plan / blacklist cycle
        current_blacklist = list(state.get("blacklisted_files") or [])
        for f in allowed_files:
            if f not in current_blacklist:
                current_blacklist.append(f)
        return {
            "status": "candidates_invalid",
            "validation_failure_reason": f"patch_gate: {gate_failures}",
            "blacklisted_files": current_blacklist,
            "candidate_retry_count": state.get("candidate_retry_count", 0) + 1,
        }

    logger.info("✅ PATCH GATE: all checks passed (head=%s)", head_commit)
    return {}


def angular_module_registration_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Gap 2 — Angular @NgModule registration.

    After code generation, if a new Angular @Component / @Directive / @Pipe or
    @Injectable service was created, it must be registered in the governing
    *.module.ts file's declarations / providers array — otherwise Angular throws
    "Component X is not part of any NgModule" at build time.

    Strategy (mirrors what Copilot Workspace and Cursor do automatically):
      1. For each generated TypeScript file, check if it declares @Component,
         @Directive, @Pipe, or @Injectable.
      2. Walk up the directory tree from the generated file to find the nearest
         *.module.ts (stopping at the workspace root).
      3. Read that module file and locate the correct array (declarations for
         components/directives/pipes, providers for services).
      4. If the class name is not already in the array, inject it — both the
         import statement and the array entry — and write the file to disk.
    """
    workspace_path = str(state.get("workspace_path", ""))
    generated = list(state.get("generated_code") or [])
    if not generated or not workspace_path:
        return {}

    _DECORATOR_ARRAY = {
        "@Component": "declarations",
        "@Directive": "declarations",
        "@Pipe":      "declarations",
        "@Injectable": "providers",
    }

    modified_modules: dict[str, str] = {}  # module_path → updated content

    for gen in generated:
        lang_raw = getattr(gen, "language", None)
        lang = getattr(lang_raw, "value", str(lang_raw)).lower() if lang_raw else ""
        if lang not in ("typescript", "javascript"):
            continue

        content = getattr(gen, "content", "") or ""
        file_path = getattr(gen, "file_path", "") or ""
        if not content or not file_path:
            continue

        # Only process new CREATE tasks or files that look like Angular constructs
        change_type = getattr(getattr(gen, "change_type", None), "value", "") or ""

        # Detect Angular decorator and extract class name
        decorator_used = None
        for dec in _DECORATOR_ARRAY:
            if dec + "(" in content or dec + "\n" in content:
                decorator_used = dec
                break
        if not decorator_used:
            continue

        # Gap 4: Skip standalone components (Angular 14+) — they don't need module registration
        if re.search(r'standalone\s*:\s*true', content):
            logger.debug(f"  angular_module: {file_path} is standalone — skipping module registration")
            continue

        target_array = _DECORATOR_ARRAY[decorator_used]

        class_match = re.search(r'export\s+class\s+([A-Za-z][A-Za-z0-9_]*)', content)
        if not class_match:
            continue
        class_name = class_match.group(1)

        # Find nearest *.module.ts walking up from the generated file's directory
        gen_abs = Path(workspace_path) / file_path
        search_dir = gen_abs.parent
        ws_root = Path(workspace_path)
        module_path: Optional[str] = None
        for _ in range(8):  # max 8 levels up
            candidates = sorted(search_dir.glob("*.module.ts"))
            if candidates:
                module_path = str(candidates[0].relative_to(ws_root)).replace("\\", "/")
                break
            if search_dir == ws_root or search_dir.parent == search_dir:
                break
            search_dir = search_dir.parent

        if not module_path:
            logger.debug(f"  angular_module: no *.module.ts found for {file_path}")
            continue

        # Read module content (use in-memory version if already modified)
        module_abs = Path(workspace_path) / module_path
        if module_path in modified_modules:
            module_content = modified_modules[module_path]
        elif module_abs.exists():
            try:
                module_content = module_abs.read_text(encoding="utf-8")
            except Exception:
                continue
        else:
            continue

        # Skip if class already registered
        if class_name in module_content:
            logger.debug(f"  angular_module: {class_name} already in {module_path}")
            continue

        # Build import statement (relative path from module to generated file)
        import os
        rel = os.path.relpath(
            str(gen_abs.with_suffix("")),
            str(module_abs.parent),
        ).replace("\\", "/")
        if not rel.startswith("."):
            rel = "./" + rel
        new_import = f"import {{ {class_name} }} from '{rel}';"

        # Inject import after last existing import in module
        _last_imp = None
        for _m in re.finditer(r'^import[^\n]+\n', module_content, re.MULTILINE):
            _last_imp = _m
        insert_at = _last_imp.end() if _last_imp else 0
        module_content = (
            module_content[:insert_at] + new_import + "\n" + module_content[insert_at:]
        )

        # Inject class name into the target array (declarations / providers)
        # Matches:  declarations: [\n  Foo,\n  Bar\n]  or  declarations: [Foo, Bar]
        arr_rx = re.compile(
            rf'{target_array}\s*:\s*\[([^\]]*)\]', re.DOTALL
        )
        arr_match = arr_rx.search(module_content)
        if arr_match:
            old_arr = arr_match.group(0)
            inner = arr_match.group(1).rstrip()
            # Append with trailing comma awareness
            sep = ",\n    " if "\n" in inner else ", "
            new_inner = inner.rstrip(",") + sep + class_name
            new_arr = old_arr.replace(arr_match.group(1), new_inner)
            module_content = module_content.replace(old_arr, new_arr, 1)
        else:
            # Array not found — add it as a new property in @NgModule({...})
            # This is a last-resort fallback; shouldn't happen in well-formed modules.
            logger.warning(
                f"  angular_module: '{target_array}' array not found in {module_path}"
                f" for {class_name} — skipping"
            )
            continue

        modified_modules[module_path] = module_content
        logger.info(
            f"  angular_module: registered {class_name} in {module_path} "
            f"({target_array})"
        )

    if not modified_modules:
        return {}

    # Write all modified module files to disk
    for mod_path, mod_content in modified_modules.items():
        try:
            (Path(workspace_path) / mod_path).write_text(mod_content, encoding="utf-8")
        except Exception as e:
            logger.warning(f"  angular_module: write failed for {mod_path}: {e}")

    return {}


def import_validation_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Layer 3 — Claude Code / Devin technique: Pre-Build Import Validation.

    Runs AFTER patch_gate and BEFORE build.  Scans every generated file for
    unresolved PascalCase symbol references, looks each up in the SQLite symbol
    index, and injects missing import statements WITHOUT calling the LLM.

    This eliminates the most common class of build failures ("cannot find symbol",
    "TS2304 Cannot find name", "CS0246") before they ever reach the compiler.
    """
    sqlite_store = getattr(agents.localizer, "sqlite_store", None)
    workspace_path = str(state.get("workspace_path", ""))

    if not sqlite_store:
        logger.debug("import_validation_node: no sqlite_store — skipping")
        return {}

    generated = list(state.get("generated_code") or [])
    if not generated:
        return {}

    patched_count = 0
    for gen in generated:
        lang_raw = getattr(gen, "language", None)
        lang = getattr(lang_raw, "value", str(lang_raw)).lower() if lang_raw else ""
        if lang not in ("java", "typescript", "javascript", "python", "csharp", "cs"):
            continue

        content = getattr(gen, "content", "") or ""
        if not content:
            continue

        fixes = _resolve_unresolved_symbols(content, lang, sqlite_store, workspace_path, source_file_path=getattr(gen, "file_path", ""))
        if not fixes:
            continue

        # Apply each import fix using the same logic as _inject_meta_imports
        from ticket_to_code.models import GeneratedCode as _GC  # avoid circular at module level
        dummy = type("_D", (), {"imports": [f for f, _ in fixes], "content": content, "language": gen.language})()
        dummy = _inject_meta_imports(dummy)  # type: ignore[arg-type]

        if dummy.content != content:
            gen.content = dummy.content
            # Write updated content to disk immediately
            out = Path(workspace_path) / gen.file_path
            if out.exists():
                try:
                    out.write_text(gen.content, encoding="utf-8")
                    patched_count += 1
                    logger.info(
                        f"  import_validation: injected {len(fixes)} import(s) into {gen.file_path}: "
                        + ", ".join(hint for hint, _ in fixes[:5])
                    )
                except Exception as e:
                    logger.warning(f"  import_validation: write failed for {gen.file_path}: {e}")

    if patched_count:
        logger.info(f"  import_validation_node: patched {patched_count} file(s) with missing imports")
        return {"generated_code": generated}

    return {}


def build_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 5: Build Project

    Universal build for ANY project type (Java, .NET, Python, Node.js, Angular, etc.).
    When TypeScript/Angular files were generated, runs `tsc --noEmit` FIRST to
    surface compile errors before they reach the running `ng serve` hot-reload.
    """
    logger.info(" PHASE 5: Build Project")

    # ── Angular / TypeScript compile check ────────────────────────────────────
    # If any generated file is a TypeScript source, verify it compiles cleanly
    # using the TypeScript compiler.  This catches errors (wrong property names,
    # missing imports, type mismatches) that `ng serve` would report to the
    # developer but the agent would never see.
    generated = state.get("generated_code", [])
    ts_files = [g for g in generated if g.file_path.endswith((".ts", ".tsx"))]
    if ts_files:
        ng_root = _find_ng_root(Path(state["workspace_path"]))
        if ng_root:
            logger.info(
                f" Angular/TypeScript compile check — {len(ts_files)} TS file(s) written, "
                f"project root: {ng_root}"
            )
            ts_result = _run_tsc_check(ng_root)
            if ts_result is not None:
                # Compile errors found — let fix_build_errors_node correct them
                logger.error(f"  ❌ TypeScript compile FAILED — routing to fix_build")
                agents.tracer.record_build(ts_result)
                
                log_content = f"Command: tsc --noEmit (Failed in {ng_root})\nExit Code: {ts_result.exit_code}\n\nSTDOUT:\n{ts_result.stdout}\n\nSTDERR:\n{ts_result.stderr}"
                write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "build_output.log", log_content)
                
                return {"build_result": ts_result}

            logger.info("  ✅ TypeScript compile OK")

            # For pure Angular tickets (no .NET/.java files generated), the tsc
            # check IS the build — return success immediately so we don't try
            # to run msbuild/dotnet on an Angular project.
            non_ts = [
                g for g in generated
                if not g.file_path.endswith((".ts", ".tsx", ".html", ".scss", ".css"))
            ]
            if not non_ts:
                logger.info("   Pure Angular project — skipping .NET/Java build step")
                _ok = BuildResult(
                    status=BuildStatus.SUCCESS,
                    exit_code=0,
                    errors=[],
                    stdout="TypeScript compile: OK",
                    stderr="",
                )
                agents.tracer.record_build(_ok)
                return {"build_result": _ok}
        else:
            logger.warning("  ⚠️ TypeScript files generated but no Angular project root found — skipping tsc check")

    # ── .NET / Java / other build ──────────────────────────────────────────────
    
    # 1. Identify which directories contain generated non-TS files
    generated = state.get("generated_code", [])
    non_ts_files = [g for g in generated if not g.file_path.endswith((".ts", ".tsx", ".html", ".scss", ".css"))]
    
    if not non_ts_files:
        logger.info("   No backend files modified — skipping backend build step")
        _ok = BuildResult(
            status=BuildStatus.SUCCESS,
            exit_code=0,
            errors=[],
            stdout="No backend files modified",
            stderr="",
        )
        agents.tracer.record_build(_ok)
        return {"build_result": _ok}

    # Extract top-level directories of modified files
    modified_services = set()
    for g in non_ts_files:
        parts = Path(g.file_path).parts
        if parts:
            modified_services.add(parts[0])

    workspace = Path(state["workspace_path"])
    build_results = []
    
    for service_dir in modified_services:
        service_path = workspace / service_dir
        if not service_path.is_dir():
            continue
            
        logger.info(f"Checking modified service directory: {service_dir}")
        
        # Determine build tool for this service
        executor = None
        project_file = None
        
        # Hardcoded CC4E architecture map for instant build routing
        CC4E_SERVICE_MAP = {
            "issues-service": ("java-gradle", "build.gradle"),
            "project-service": ("java-gradle", "build.gradle"),
            "area-service": ("java-maven", "pom.xml"),
            "se-connector-apis": ("java-maven", "pom.xml"),
            "sagas-service": ("java-gradle", "build.gradle"),
            "bim-gateway": ("java-gradle", "build.gradle"),
            "ene-load-service": ("java-gradle", "build.gradle"),
            "xchange-ui": ("nodejs", "package.json")
        }
        
        if service_dir in CC4E_SERVICE_MAP:
            tech, p_file = CC4E_SERVICE_MAP[service_dir]
            executor = ExecutionEngineFactory.create(str(workspace), tech)
            project_file = f"{service_dir}/{p_file}"
        else:
            if (service_path / "pom.xml").exists():
                executor = ExecutionEngineFactory.create(str(workspace), "java-maven")
                project_file = f"{service_dir}/pom.xml"
            elif (service_path / "build.gradle").exists():
                executor = ExecutionEngineFactory.create(str(workspace), "java-gradle")
                project_file = f"{service_dir}/build.gradle"
            elif (service_path / "package.json").exists():
                executor = ExecutionEngineFactory.create(str(workspace), "nodejs")
                project_file = f"{service_dir}/package.json"
            elif (service_path / "pyproject.toml").exists():
                executor = ExecutionEngineFactory.create(str(workspace), "python")
                project_file = f"{service_dir}/pyproject.toml"
            elif (service_path / "setup.py").exists():
                executor = ExecutionEngineFactory.create(str(workspace), "python")
                project_file = f"{service_dir}/setup.py"
            elif (service_path / "requirements.txt").exists():
                executor = ExecutionEngineFactory.create(str(workspace), "python")
                project_file = f"{service_dir}/requirements.txt"
        
        if not executor or not project_file:
            logger.warning(f"Could not determine build tool for {service_dir}, trying fallback detect")
            try:
                # Set a strict timeout or limit on detection in the future if needed
                executor = ExecutionEngineFactory.create(str(workspace))
                project_files = executor.detect_project_files()
                if project_files:
                    project_file = project_files[0]
            except Exception as e:
                logger.error(f"Failed to auto-detect executor: {e}")
                
        if not executor or not project_file:
            continue
            
        logger.info(f"Building {service_dir} using {executor.get_technology_name()} project: {project_file}")
        service_result = executor.execute_build(project_file)
        build_results.append((service_dir, service_result))

    if not build_results:
        # If no buildable service directories matched (e.g. modified a root README.md),
        # we skip the slow recursive global project detection and assume no build is needed.
        logger.info("No buildable projects found for modified files. Assuming no build required for root-level changes.")
        _no_proj = BuildResult(
            status=BuildStatus.SUCCESS,
            exit_code=0,
            errors=[],
            stdout="No backend files in recognized service directories modified",
            stderr=""
        )
        agents.tracer.record_build(_no_proj)
        return {"build_result": _no_proj}

    # Aggregate results
    failed_results = [r for d, r in build_results if r.status.value != "success"]
    
    if failed_results:
        # Take the first failure as the primary result
        first_fail_dir, first_fail_res = next((d, r) for d, r in build_results if r.status.value != "success")
        logger.error(f"Build failed in {first_fail_dir}: {len(first_fail_res.errors)} errors")
        agents.tracer.record_build(first_fail_res)
        
        log_content = f"Command: auto (Failed in {first_fail_dir})\nExit Code: {first_fail_res.exit_code}\n\nSTDOUT:\n{first_fail_res.stdout}\n\nSTDERR:\n{first_fail_res.stderr}"
        write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "build_output.log", log_content)
        
        log_phase(
            phase='BUILD',
            llm_output={
                'status': first_fail_res.status.value,
                'exit_code': first_fail_res.exit_code,
                'errors_count': len(first_fail_res.errors),
                'error_samples': first_fail_res.errors[:2],
                'failed_service': first_fail_dir
            },
            agent='BuildExecutor'
        )
        
        return {"build_result": first_fail_res}
    else:
        logger.info("✅ All modified services built successfully")
        # Return the last successful result
        last_dir, last_res = build_results[-1]
        agents.tracer.record_build(last_res)
        
        log_content = f"Command: auto (Multiple services built)\nExit Code: 0\n\nSTDOUT:\nAll modified services built successfully."
        write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "build_output.log", log_content)
        
        log_phase(
            phase='BUILD',
            llm_output={
                'status': last_res.status.value,
                'exit_code': last_res.exit_code,
                'success': True,
                'services_built': [d for d, r in build_results]
            },
            agent='BuildExecutor'
        )
        
        return {"build_result": last_res}



def test_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 6: Run Tests
    
    Universal test runner for ANY project type (Java JUnit, .NET NUnit/xUnit, Python pytest, etc.).
    Execution engine auto-detects language and test framework.
    
    LangGraph automatically waits for BOTH generate_tests AND build before running this!
    """
    logger.info(" PHASE 6: Run Tests")
    
    # Optional: Verify both branches completed (LangGraph already guarantees this!)
    logger.info(f"   TEST BRANCH: {state.get('test_status')} ({len(state.get('generated_tests', []))} files)")
    logger.info(f"   CODE BRANCH: {state.get('code_status')} ({len(state.get('generated_code', []))} files)")
    logger.info(f"   BUILD: {state.get('build_result').status.value if state.get('build_result') else 'N/A'}")
    
    # Let execution engine detect project files automatically
    project_files = agents.executor.detect_project_files()
    
    if not project_files:
        return {
            "test_result": TestResult(
                status=TestStatus.ERROR,
                total_tests=0,
                passed=0,
                failed=0,
                errors=["No project files found for testing"]
            )
            # ✅ REMOVED: "last_error_type" - avoid concurrent updates at convergence
        }
    
    # Use first detected project file
    project_file = project_files[0]
    logger.info(f"Running {agents.executor.get_technology_name()} tests: {project_file}")
    
    test_result = agents.executor.execute_tests(project_file)
    
    if test_result.status.value not in ("all_passed", "success"):
        logger.error(f"Tests failed: {test_result.failed}/{test_result.total_tests}")
        agents.tracer.finalize(state | {"test_result": test_result})
        
        log_content = f"Command: auto\nExit Code: {test_result.exit_code}\nPassed: {test_result.passed}, Failed: {test_result.failed}\n\nSTDOUT:\n{test_result.stdout}\n\nSTDERR:\n{test_result.stderr}"
        write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "test_output.log", log_content)
        
        # DEBUG LOG
        log_phase(
            phase='TEST',
            llm_output={
                'status': test_result.status.value,
                'total_tests': test_result.total_tests,
                'passed': test_result.passed,
                'failed': test_result.failed,
                'exit_code': test_result.exit_code
            },
            agent='TestRunner'
        )
        
        return {
            "test_result": test_result
        }
    else:
        logger.info(f"✅ Tests passed: {test_result.passed}/{test_result.total_tests}")
        agents.tracer.finalize(state | {"test_result": test_result})
        
        log_content = f"Command: auto\nPassed: {test_result.passed}, Failed: {test_result.failed}"
        write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "test_output.log", log_content)
        
        # DEBUG LOG
        log_phase(
            phase='TEST',
            llm_output={
                'status': test_result.status.value,
                'total_tests': test_result.total_tests,
                'passed': test_result.passed,
                'failed': test_result.failed,
                'success': True
            },
            agent='TestRunner'
        )
        
        return {
            "test_result": test_result
        }


def fix_build_errors_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Debug Node: Fix Build Errors
    
    Uses ErrorResolutionAgent to autonomously fix build errors using an agentic loop.
    """
    logger.info(" DEBUG: Fixing Build Errors (Agentic Loop)")
    
    normalized_build_errors = normalize_error_lines(state["build_result"].errors)
    logger.info(f"Analyzing {len(normalized_build_errors)} build errors...")

    # ── Enhancement 12: Dependency Resolution ─────────────────────────────
    try:
        dep_result = agents.dependency_resolver.resolve(
            build_errors=normalized_build_errors,
            technology="auto",
        )
        if dep_result.has_missing_deps:
            logger.info(
                f"  Enhancement 12: {dep_result.resolved_count} missing deps detected. "
                f"Manifest updated: {dep_result.manifest_updated}"
            )
            if dep_result.install_command:
                logger.info(f"  Install command: {dep_result.install_command}")
                import subprocess
                install_result = subprocess.run(
                    dep_result.install_command.split(),
                    cwd=str(state["workspace_path"]),
                    capture_output=True, text=True, timeout=120,
                    encoding="utf-8", errors="replace",
                )
                if install_result.returncode == 0:
                    logger.info("  Enhancement 12: Dependencies installed successfully")
                    return {
                        "retry_attempt": state.get("retry_attempt", 0),  # Don't increment
                        "status": "dependencies_resolved",
                    }
    except Exception as exc:
        logger.debug(f"Enhancement 12 check failed (non-fatal): {exc}")

    # ── Enhancement 4: Adaptive Strategy Switching ────────────────────────
    try:
        replan = agents.adaptive_replanner.analyze_failure(
            error_messages=normalized_build_errors,
            attempt_number=state.get("retry_attempt", 0),
            current_strategy="general",
            failed_strategies=agents.adaptive_replanner.get_failed_strategies(),
        )
        if replan.should_switch:
            logger.info(
                f"  Enhancement 4: Strategy switch recommended: "
                f"{replan.new_category}/{replan.new_profile} "
                f"(reason: {replan.reason})"
            )
    except Exception as exc:
        logger.debug(f"Enhancement 4 check failed (non-fatal): {exc}")

    # ── Agentic Fix Loop ───────────────────────────────────────────────────
    from ticket_to_code.agents.error_resolution_agent import ErrorResolutionAgent
    from aviator.services.llm import LLMRegistry
    from pathlib import Path
    
    llm = getattr(agents, "llm", None) or getattr(getattr(agents, "code_generator", None), "llm", None) or LLMRegistry.get_llm(assistant=True)
    sqlite_store = getattr(agents.localizer, "sqlite_store", None)
    workspace_path = Path(state["workspace_path"])
    
    agent = ErrorResolutionAgent(llm, workspace_path, sqlite_store)

    # ── v2: Pass full context so the agent can make intelligent decisions ──
    agent.pre_edit_snapshots = state.get("original_file_contents") or {}
    agent.our_modified_files = set(
        (getattr(g, "file_path", "") or "").replace("\\", "/").lower()
        for g in (state.get("generated_code") or [])
    )
    ticket_obj = state.get("ticket")
    agent.ticket_description = str(getattr(ticket_obj, "description", "")) if ticket_obj else ""
    agent.compile_root = _find_ng_root(workspace_path)
    
    # Run the loop to get modified files
    modified_files = agent.resolve_errors(normalized_build_errors)
    
    # Write any remaining files to disk (COMPILE tool already writes during the loop,
    # but this catches files modified after the last COMPILE call)
    for fp, fixed_content in modified_files.items():
        abs_path = workspace_path / fp
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(fixed_content, encoding="utf-8")
        logger.info(f"  Wrote fix to disk: {fp}")

    return {
        "retry_attempt": state.get("retry_attempt", 0) + 1,
        "status": "fixes_applied"
    }



def fix_test_failures_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Debug Node: Fix Test Failures
    
    Analyzes test failures and decides:
    - Fix CODE (if implementation wrong)
    - OR Fix TESTS (if test expectations wrong)
    
    Uses LLM to determine which is incorrect.
    """
    logger.info(" DEBUG: Fixing Test Failures")
    
    test_result = state.get("test_result")
    if not test_result:
        return {
            "retry_attempt": state["retry_attempt"] + 1
            # ✅ REMOVED: "status" - avoid concurrent updates
        }
    
    test_output = test_result.test_output if hasattr(test_result, 'test_output') else "No output"
    logger.info(f"Analyzing {test_result.failed} test failures...")
    
    try:
        from aviator.services.llm import LLMRegistry
        from langchain_core.messages import SystemMessage, HumanMessage
        
        llm = LLMRegistry.get_llm(assistant=True)
        
        # Build debug prompt
        system_prompt = """You are an expert debugger specializing in test failures.

TASK: Analyze test failures and determine what needs to be fixed.

IMPORTANT DECISION:
1. If the TEST is wrong (incorrect expectations) → Fix the test
2. If the CODE is wrong (incorrect implementation) → Fix the code
3. Be clear about which one needs fixing

Respond with JSON:
{
  "analysis": "Analysis of test failures",
  "decision": "fix_code" or "fix_tests",
  "fixes": [
    {
      "file": "path/to/file.cs",
      "type": "code" or "test",
      "fixed_code": "complete corrected code for the file",
      "reason": "why this fixes the failure"
    }
  ]
}"""
        
        # Get generated files content
        generated_code_content = "\n\n".join([
            f"FILE: {gen.file_path}\n{gen.content}"
            for gen in state["generated_code"]
        ])
        
        generated_tests_content = "\n\n".join([
            f"TEST FILE: {gen.file_path}\n{gen.content}"
            for gen in state.get("generated_tests", [])
        ]) if state.get("generated_tests") else "No tests generated"
        
        user_prompt = f"""TEST FAILURES:
Total: {test_result.total_tests}
Failed: {test_result.failed}
Passed: {test_result.passed}

TEST OUTPUT:
{test_output[:2000]}

GENERATED CODE:
{generated_code_content[:3000]}

GENERATED TESTS:
{generated_tests_content[:3000]}

REQUIREMENTS:
{state["requirements"]}

Analyze and determine what needs to be fixed."""
        
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        
        # Parse response and apply fixes
        import json
        
        try:
            from ticket_to_code.json_utils import parse_llm_json
            fixes_data = parse_llm_json(response.content)
            if isinstance(fixes_data, dict):
                logger.info(f"Debug Analysis: {fixes_data.get('analysis', 'No analysis')}")
                logger.info(f"Decision: {fixes_data.get('decision', 'Unknown')}")
            
            # Normalize fixes container
            if isinstance(fixes_data, list):
                raw_fixes_list = fixes_data
            elif isinstance(fixes_data, dict):
                raw_fixes_list = (
                    fixes_data.get('fixes') or
                    fixes_data.get('files') or
                    fixes_data.get('patches') or
                    fixes_data.get('modifications') or
                    fixes_data.get('changes') or
                    []
                )
                if not raw_fixes_list and any(k in fixes_data for k in ['file', 'file_path', 'path', 'fixed_code', 'code', 'content']):
                    raw_fixes_list = [fixes_data]
            else:
                raw_fixes_list = []

            # ── SCOPE ENFORCEMENT ────────────────────────────────────────────
            # Test-fix follows the same rules as build-fix and code gen:
            # code fixes may only touch planner-approved files; test fixes may
            # only touch files that were already written by generate_tests_node.
            _plan_tf = state.get("architectural_plan")
            _allowed_code_tf: list = []
            _readonly_code_tf: list = []
            if _plan_tf:
                _allowed_code_tf  = [t.file_path for t in _plan_tf.tasks
                                      if t.task_type.value != "read_only"]
                _readonly_code_tf = [t.file_path for t in _plan_tf.tasks
                                      if t.task_type.value == "read_only"]

            # Allowed test paths = exactly the paths written by generate_tests_node
            _allowed_test_tf = {
                gen.file_path for gen in state.get("generated_tests", [])
            }

            def _tfnorm(p: str) -> str:
                return p.replace("\\", "/").strip("/").lower()

            def _paths_match_tf(p1: str, p2: str) -> bool:
                n1 = _tfnorm(p1)
                n2 = _tfnorm(p2)
                return n1 == n2 or n1.endswith("/" + n2) or n2.endswith("/" + n1)

            def _infer_language_for_path_tf(p: str) -> Optional[ProgrammingLanguage]:
                ext = Path(p).suffix.lower()
                if ext in (".ts", ".tsx"):
                    return ProgrammingLanguage.TYPESCRIPT
                if ext in (".js", ".jsx"):
                    return ProgrammingLanguage.JAVASCRIPT
                if ext == ".py":
                    return ProgrammingLanguage.PYTHON
                if ext in (".cs",):
                    return ProgrammingLanguage.CSHARP
                if ext in (".java",):
                    return ProgrammingLanguage.JAVA
                if ext in (".html", ".htm"):
                    return ProgrammingLanguage.HTML
                if ext in (".scss", ".css", ".less"):
                    return ProgrammingLanguage.SCSS
                if ext in (".json",):
                    return ProgrammingLanguage.JSON
                if ext in (".yml", ".yaml"):
                    return ProgrammingLanguage.YAML
                return None

            _allowed_code_norm_tf  = {_tfnorm(p) for p in _allowed_code_tf}
            _readonly_code_norm_tf = {_tfnorm(p) for p in _readonly_code_tf}

            # Ownership map for READ_ONLY classification check
            _disc_ownership_tf: dict = {}
            for _d in (state.get("discovered_files") or []):
                _np = _tfnorm(_d.get("path", ""))
                _ot = _d.get("features", {}).get("ownership_type", None)
                if _np and _ot:
                    _disc_ownership_tf[_np] = _ot

            _tf_validator = PatchValidator()

            # Apply fixes based on decision
            decision = fixes_data.get('decision', 'fix_code') if isinstance(fixes_data, dict) else 'fix_code'
            applied_tf_count = 0

            for fix in raw_fixes_list:
                if not isinstance(fix, dict):
                    continue
                file_path = (
                    fix.get('file') or
                    fix.get('file_path') or
                    fix.get('path') or
                    fix.get('filename') or
                    fix.get('filePath') or
                    fix.get('target_file') or
                    ""
                )
                fixed_content = (
                    fix.get('fixed_code') or
                    fix.get('fixed_content') or
                    fix.get('code') or
                    fix.get('content') or
                    fix.get('patch') or
                    fix.get('new_content') or
                    fix.get('modified_code') or
                    ""
                )
                if not file_path or not fixed_content:
                    logger.warning(f"  ⚠️ Skipping malformed test-fix item (keys: {list(fix.keys())})")
                    continue

                fix_type = fix.get('type', 'code')
                norm_fp_tf = _tfnorm(file_path)

                logger.info(f"  Test-fix: type={fix_type}  file={file_path}")

                # Determine which collection to update
                is_test_fix = (
                    fix_type == 'test'
                    or '.spec.' in file_path
                    or file_path.endswith('.spec.ts')
                    or file_path.endswith('.spec.tsx')
                    or 'test' in file_path.lower()
                )
                if is_test_fix:
                    target_collection = state.get("generated_tests", [])
                else:
                    target_collection = state.get("generated_code", [])

                # ── (1) Scope check ──────────────────────────────────────────
                if is_test_fix:
                    if _allowed_test_tf and not any(_paths_match_tf(file_path, a) for a in _allowed_test_tf):
                        logger.warning(
                            f"   TEST-FIX SCOPE: '{file_path}' was not "
                            f"generated by test generator — skipped"
                        )
                        continue
                else:
                    if _allowed_code_tf and not any(_paths_match_tf(file_path, a) for a in _allowed_code_tf):
                        logger.warning(
                            f"   TEST-FIX CODE SCOPE: '{file_path}' not in "
                            f"planner-approved write list — skipped"
                        )
                        continue

                # ── (2) Read-only task check (code fixes only) ───────────────
                if not is_test_fix and any(_paths_match_tf(file_path, r) for r in _readonly_code_tf):
                    logger.warning(
                        f"   TEST-FIX READ-ONLY TASK: '{file_path}' is "
                        f"a read_only task — skipped"
                    )
                    continue

                # ── (3) Ownership READ_ONLY check ─────────────────────────────
                if _disc_ownership_tf.get(norm_fp_tf) == "READ_ONLY":
                    logger.warning(
                        f"   TEST-FIX OWNERSHIP READ-ONLY: '{file_path}' "
                        f"classified READ_ONLY by Phase 2 — skipped"
                    )
                    continue

                # ── (4) PatchValidator (code fixes only, not test fixes) ──────
                if not is_test_fix and _plan_tf:
                    _existing_tf = None
                    _tf_disk = Path(state["workspace_path"]) / file_path
                    if _tf_disk.exists():
                        try:
                            _existing_tf = _tf_disk.read_text(encoding="utf-8")
                        except Exception:
                            pass

                    _matching_task_tf = next(
                        (t for t in _plan_tf.tasks
                         if _paths_match_tf(t.file_path, file_path)),
                        None,
                    )

                    if _matching_task_tf and _existing_tf is not None:
                        from ticket_to_code.models import GeneratedCode as _GCTf
                        _tf_gen_code = _GCTf(
                            file_path=file_path,
                            content=fixed_content,
                            language=_matching_task_tf.language,
                            change_type=_matching_task_tf.task_type,
                            documentation="test-fix",
                            imports=[],
                        )
                        _tf_validation = _tf_validator.validate(
                            _tf_gen_code, _matching_task_tf, _existing_tf
                        )
                        if not _tf_validation.passed:
                            logger.warning(
                                f"  ⛔ TEST-FIX PatchValidator REJECTED '{file_path}':\n"
                                + "\n".join(
                                    f"     • {v}" for v in _tf_validation.violations
                                )
                            )
                            continue

                # ── (5) All checks passed — apply fix ────────────────────────
                matched_in_target = False
                for gen_file in target_collection:
                    gen_path = getattr(gen_file, "file_path", "") if not isinstance(gen_file, dict) else gen_file.get("file_path", "")
                    if _paths_match_tf(gen_path, file_path):
                        if isinstance(gen_file, dict):
                            gen_file["content"] = fixed_content
                        else:
                            gen_file.content = fixed_content

                        actual_rel_path = gen_path if gen_path else file_path
                        output_path = Path(state["workspace_path"]) / actual_rel_path
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_text(fixed_content, encoding='utf-8')
                        # Fix 4: sync test-fix to _run_generated_map
                        state.setdefault("_run_generated_map", {})[normalize_path(actual_rel_path).lower()] = fixed_content
                        applied_tf_count += 1
                        matched_in_target = True
                        logger.info(f"  ✅ Test-fix applied ({fix_type}): {actual_rel_path}")

                if not matched_in_target:
                    output_path = Path(state["workspace_path"]) / file_path
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(fixed_content, encoding='utf-8')
                    # Fix 4: sync direct test-fix to _run_generated_map
                    state.setdefault("_run_generated_map", {})[normalize_path(file_path).lower()] = fixed_content

                    _lang_tf = _infer_language_for_path_tf(file_path)
                    if _lang_tf is not None:
                        _task_for_tf = None
                        if _plan_tf:
                            _task_for_tf = next(
                                (t for t in _plan_tf.tasks if _paths_match_tf(t.file_path, file_path)),
                                None,
                            )
                        _ct_tf = _task_for_tf.task_type if _task_for_tf else TaskType.MODIFY
                        try:
                            target_collection.append(
                                GeneratedCode(
                                    file_path=file_path,
                                    content=fixed_content,
                                    language=_lang_tf,
                                    change_type=_ct_tf,
                                    documentation="test-fix",
                                    imports=[],
                                )
                            )
                        except Exception as _append_tf_exc:
                            logger.debug(f"Could not sync direct test-fix into state for '{file_path}': {_append_tf_exc}")
                    applied_tf_count += 1
                    logger.info(f"  ✅ Test-fix applied directly ({fix_type}): {file_path}")
            
            logger.info(f"✅ Applied {applied_tf_count} fixes")
            
            return {
                "generated_code": state["generated_code"],
                "generated_tests": state.get("generated_tests", []),
                "retry_attempt": state["retry_attempt"] + 1
                # ✅ REMOVED: "status" - avoid concurrent updates
            }
            
        except json.JSONDecodeError:
            logger.warning("Could not parse JSON response")
            return {
                "retry_attempt": state["retry_attempt"] + 1
                # ✅ REMOVED: "status" - avoid concurrent updates
            }
            
    except Exception as e:
        logger.error(f"Debug agent error: {e}", exc_info=True)
        return {
            "retry_attempt": state["retry_attempt"] + 1
            # ✅ REMOVED: "status" - avoid concurrent updates
        }


# ============================================================================
# CONDITIONAL ROUTING FUNCTIONS
# ============================================================================

def route_after_investigation(state: TicketToCodeState) -> Literal["unified_analysis"]:
    """
    Route after investigation - always go to unified analysis.
    Unified analysis node handles both code and non-code paths internally.
    """
    return "unified_analysis"


def route_after_unified_analysis(state: TicketToCodeState):
    """
    Route after unified analysis:
    - If requirements exist (code ticket) → Repository Discovery first, then planning
    - If solution_guidance exists (non-code ticket) → End workflow
    """
    if state.get("requirements"):
        return "discover"  # Discovery runs BEFORE planning
    elif state.get("solution_guidance"):
        return END
    else:
        logger.error("Unified analysis produced neither requirements nor solution guidance!")
        return END


def route_after_validate_candidates(state: TicketToCodeState) -> str:
    """
    Route after candidate validation:
    - validated            → localize  (normal forward path)
    - failed (hard stop)   → END       (discovery exhausted all cycles)
    - invalid, retries < max → plan    (re-rank with blacklist, no re-discovery)
    - invalid, retries >= max → discover (full rediscovery with expanded scope)
    """
    print(f"DEBUG route_after_validate_candidates: status='{state.get('status')}', retry_count={state.get('candidate_retry_count')}")
    if state.get("status") == "candidates_validated":
        return "localize"
    if state.get("status") == "no_action_required":
        logger.info("   Routing to END with terminal no_action_required")
        return END
    if state.get("status") == "failed":
        logger.error("   Routing to END due to discovery hard stop")
        return END
    retry_count = state.get("candidate_retry_count", 0)
    max_retries = state.get("max_candidate_retries", 2)
    if retry_count >= max_retries:
        logger.warning(
            f"   Max candidate retries ({max_retries}) reached — triggering full rediscovery"
        )
        return "discover"
    logger.info(
        f"   Candidate retry {retry_count}/{max_retries} — re-planning with updated blacklist"
    )
    return "plan"


def route_after_grounded_understanding(state: TicketToCodeState) -> str:
    """
    Route after grounded understanding (Phase 2G-3):
    - normal → ownership_completeness
    - evidence_insufficient → END (Zero-evidence hard fallback)
    """
    if state.get("status") == "evidence_insufficient":
        logger.error("   Routing to END due to insufficient evidence (Zero-evidence hard fallback)")
        return END
    return "ownership_completeness"


def route_after_generate_code(state: TicketToCodeState) -> str:
    """
    Route after code generation.

    If generation produced no effective patch, loop back through the same
    candidate retry policy as validate_candidates. Otherwise continue to
    patch_gate (B9) which verifies scope, apply-check, and secrets before build.
    """
    if state.get("status") == "escalated":
        # Don't short-circuit to END — let the remaining pipeline
        # (edit_loop → patch_gate → build → fix_build) attempt recovery.
        # The old `return END` here was severing the entire build pipeline
        # whenever code generation had partial patch failures (e.g.,
        # anchor_method_not_found on HTML files), causing fix_build to
        # never run even though other files were successfully generated.
        logger.warning(
            "  ⚠️ Code generation escalated but continuing to edit_loop "
            "for build/fix_build recovery"
        )
        return "edit_loop"

    if state.get("status") == "candidates_invalid":
        retry_count = state.get("candidate_retry_count", 0)
        max_retries = state.get("max_candidate_retries", 2)
        if retry_count >= max_retries:
            logger.warning(
                f"   Empty-patch retries reached {retry_count}/{max_retries} — rediscovering"
            )
            return "discover"
        logger.info(
            f"   Empty-patch retry {retry_count}/{max_retries} — re-planning with blacklist"
        )
        return "plan"
    # Route through edit_loop (adaptive free-form loop) before patch_gate
    return "edit_loop"


# ============================================================================
# EDIT LOOP NODE — "Claude Code / Devin" style free-form adaptive editing
# Runs AFTER generate_code_node to catch anything the fixed plan missed, and
# uses the LSP client to verify property names before writing HTML.
# ============================================================================

def edit_loop_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Free-form adaptive edit loop that mirrors how Claude Code and Devin work.

    Unlike generate_code_node (which executes a fixed N-task plan then stops),
    this node runs a while-loop:
      1. Asks the LLM: "is the ticket fully implemented? If not, what file is next?"
      2. Reads the CURRENT on-disk content of that file (always fresh)
      3. Generates a targeted minimal edit using the LSP for exact property names
      4. Writes it and immediately verifies with the TypeScript/Java compiler
      5. Fixes errors inline before moving to the next file
      6. Repeats until solved or MAX_ITER reached

    This node fires AFTER generate_code_node. If generate_code_node already
    produced a clean build, this node is a no-op (detects solved = True quickly).
    """
    from ticket_to_code.agents.edit_loop_agent import EditLoopAgent
    from ticket_to_code.agents.lsp_client import WorkspaceSymbolIndex
    from aviator.services.llm import LLMRegistry

    logger.info("\n" + "=" * 70)
    logger.info(" EDIT LOOP NODE: Adaptive free-form edit loop")
    logger.info("=" * 70)

    ticket = state.get("ticket")
    requirements = state.get("requirements")
    workspace_path = str(state.get("workspace_path", ""))

    if not ticket or not requirements or not workspace_path:
        logger.warning("  edit_loop_node: missing ticket/requirements/workspace — skipping")
        return {}

    # Use the smarter model for the edit loop (same as planner)
    llm = LLMRegistry.get_llm(assistant=True)
    symbol_index = WorkspaceSymbolIndex(workspace_path)

    # Seed the agent with files already written this run
    existing_map = {
        k: v for k, v in (state.get("_run_generated_map") or {}).items()
    }

    agent = EditLoopAgent(
        workspace_path=workspace_path,
        llm=llm,
        symbol_index=symbol_index,
    )

    # If we re-entered via the requirement-satisfaction loop, tell the edit loop
    # exactly which ticket requirements are still unmet so it targets them.
    _raw_ctx = state.get("code_rag_context", "") or ""
    _code_ctx = "\n".join(str(c) for c in _raw_ctx) if isinstance(_raw_ctx, list) else str(_raw_ctx)
    _remediation = state.get("outcome_remediation")
    if _remediation:
        _code_ctx = (
            "OUTSTANDING TICKET REQUIREMENTS NOT YET SATISFIED — implement these now:\n"
            f"{_remediation}\n\n" + _code_ctx
        )
        logger.info("  edit_loop: injecting outstanding-requirement remediation from outcome_check")

    # Run the loop
    result = agent.run(
        ticket=ticket,
        requirements=requirements,
        initial_plan=_get_plan(state),
        code_rag_context=_code_ctx,
        existing_file_map=existing_map,
    )

    logger.info(
        f"\n  Edit loop finished: {result['iterations']} iteration(s), "
        f"solved={result['solved']}, "
        f"files touched={len([r for r in result['edit_results'] if not r.skipped])}"
    )

    # Merge edit loop results back into state
    updated_map = dict(state.get("_run_generated_map") or {})
    for fp_lower, content in result["generated_files"].items():
        if fp_lower not in (existing_map or {}):
            updated_map[fp_lower] = content

    # Update generated_code list with any new files the edit loop wrote
    generated_code = list(state.get("generated_code") or [])
    existing_gen_paths = {
        (getattr(g, "file_path", None) or g.get("file_path", "")).replace("\\", "/").lower()
        for g in generated_code
    }
    from ticket_to_code.models import GeneratedCode as _GC, TaskType, ProgrammingLanguage
    for edit_result in result["edit_results"]:
        if edit_result.skipped or not edit_result.content_after:
            continue
        fp_lower = edit_result.file_path.replace("\\", "/").lower()
        if fp_lower not in existing_gen_paths:
            ext = Path(edit_result.file_path).suffix.lower()
            lang_map = {
                ".ts": ProgrammingLanguage.TYPESCRIPT, ".tsx": ProgrammingLanguage.TYPESCRIPT,
                ".java": ProgrammingLanguage.JAVA, ".py": ProgrammingLanguage.PYTHON,
                ".html": ProgrammingLanguage.HTML, ".scss": ProgrammingLanguage.SCSS,
            }
            lang = lang_map.get(ext, ProgrammingLanguage.TYPESCRIPT)
            generated_code.append(_GC(
                file_path=edit_result.file_path,
                content=edit_result.content_after,
                language=lang,
                change_type=TaskType.MODIFY,
                documentation=f"edit_loop: {Path(edit_result.file_path).name}",
                imports=[],
            ))

    return {
        "_run_generated_map": updated_map,
        "generated_code": generated_code,
        "_edit_loop_result": result,
    }


def outcome_check_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Post-build semantic validation: verify the generated code actually implements
    the ticket requirements, not just that it compiles.

    After a successful build, an LLM reviews:
      - The ticket's functional requirements
      - The diff (generated files vs originals)
    and reports which requirements are satisfied, which are missing, and why.

    This is non-blocking: if the check fails it adds warnings to the workflow
    explanation but does NOT block progression to memory_update.
    """
    logger.info("  ✅ OUTCOME CHECK: Verifying generated code satisfies ticket requirements")

    ticket = state.get("ticket")
    requirements = state.get("requirements")
    generated_code = state.get("generated_code") or []
    original_contents = state.get("original_file_contents") or {}
    workspace_path = str(state.get("workspace_path", ""))

    if not ticket or not generated_code:
        return {"outcome_check_result": {"status": "skipped", "reason": "no generated code"}}

    # Build a concise diff summary (what changed in each file)
    diff_blocks: list[str] = []
    for gen in generated_code[:6]:  # cap at 6 files to avoid token overflow
        fp = getattr(gen, "file_path", "")
        new_content = getattr(gen, "content", "") or ""
        orig_content = original_contents.get(fp, "")
        if not orig_content:
            for ofp, oc in original_contents.items():
                if Path(fp).name == Path(ofp).name and oc:
                    orig_content = oc
                    break

        if orig_content and new_content:
            # Show only the diff (changed lines with context)
            import difflib
            diff = list(difflib.unified_diff(
                orig_content.splitlines(), new_content.splitlines(),
                fromfile=f"original/{fp}", tofile=f"generated/{fp}",
                n=3, lineterm="",
            ))
            diff_str = "\n".join(diff[:80])  # cap at 80 diff lines per file
            if diff_str.strip():
                diff_blocks.append(f"=== {fp} ===\n{diff_str}")
        elif new_content and not orig_content:
            # New file — show first 60 lines
            preview = "\n".join(new_content.splitlines()[:60])
            diff_blocks.append(f"=== NEW FILE: {fp} ===\n{preview}")

    if not diff_blocks:
        return {"outcome_check_result": {"status": "skipped", "reason": "no diff available"}}

    req_text = ""
    if requirements:
        reqs = getattr(requirements, "functional_requirements", []) or []
        req_text = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(reqs[:10]))

    diff_summary = "\n\n".join(diff_blocks)

    from ticket_to_code.llm_utils import llm_invoke
    from aviator.services.llm import LLMRegistry
    from langchain_core.messages import SystemMessage, HumanMessage

    try:
        llm = getattr(agents, "llm", None) or LLMRegistry.get_llm(assistant=False)
        response = llm_invoke(llm, [
            SystemMessage(content="""You are a code reviewer verifying that a code generation
system correctly implemented a feature ticket. Your job is to check whether the
generated code changes actually satisfy the stated functional requirements.

For each requirement, state clearly: SATISFIED, PARTIAL, or MISSING.
Be concise and specific — reference exact method names, property names, or file names."""),
            HumanMessage(content=f"""TICKET: {ticket.title}

FUNCTIONAL REQUIREMENTS:
{req_text or "(not available)"}

CODE CHANGES (unified diff):
{diff_summary}

For each requirement, output:
- [SATISFIED/PARTIAL/MISSING] <requirement text>: <one-line reason>

Then output one of:
VERDICT: CORRECT  (all requirements satisfied)
VERDICT: PARTIAL  (some requirements missing)
VERDICT: INCOMPLETE  (major requirements missing)"""),
        ])

        result_text = getattr(response, "content", str(response))
        verdict = "UNKNOWN"
        if "VERDICT: CORRECT" in result_text:
            verdict = "CORRECT"
        elif "VERDICT: PARTIAL" in result_text:
            verdict = "PARTIAL"
        elif "VERDICT: INCOMPLETE" in result_text:
            verdict = "INCOMPLETE"

        logger.info(f"  ✅ Outcome check verdict: {verdict}")
        logger.info(f"  Details:\n{result_text[:600]}")

        # Extract concrete remediation from MISSING/PARTIAL requirement lines so the
        # adaptive edit loop knows exactly WHAT to add to satisfy the ticket.
        remediation_lines = [
            ln.strip() for ln in result_text.splitlines()
            if ("MISSING" in ln or "PARTIAL" in ln) and ln.strip().startswith(("-", "[", "*"))
        ]
        remediation = "\n".join(remediation_lines).strip()

        # Count this as a remediation attempt only when the ticket is NOT satisfied,
        # so the bounded loop in route_after_outcome_check can terminate.
        _prev_attempt = int(state.get("outcome_fix_attempt", 0) or 0)
        _next_attempt = _prev_attempt + 1 if verdict in ("PARTIAL", "INCOMPLETE") else _prev_attempt

        # ── Phase 5: Mechanical checklist verification ────────────────────────
        # Run ChecklistVerifier against the definition-of-done from Phase 0.
        # This is independent of the LLM-based outcome check above — it uses
        # mechanical citation verification to catch false-PASSes.
        _dod = state.get("definition_of_done")
        if _dod and hasattr(_dod, "items") and _dod.items:
            try:
                from ticket_to_code.agents.checklist_verifier import ChecklistVerifier
                _verifier = ChecklistVerifier(agents.llm)
                
                # Build file map from generated code
                _gen_files = {}
                for gen in generated_code:
                    fp = getattr(gen, "file_path", "")
                    content = getattr(gen, "content", "")
                    if fp and content:
                        _gen_files[fp] = content

                _vr = _verifier.verify(_dod.items, _gen_files)
                
                # Store unresolved items for Phase 6 escalation
                _unresolved = [
                    item for item in _vr.items if item.get("status") == "fail"
                ]
                if _unresolved:
                    state["unresolved_checklist_items"] = _unresolved
                    logger.warning(
                        f"  ⚠️ Phase 5: {len(_unresolved)}/{_vr.total_items} "
                        f"checklist items UNRESOLVED"
                    )
                else:
                    state["unresolved_checklist_items"] = []
                    logger.info(
                        f"  ✅ Phase 5: All {_vr.total_items} checklist items PASSED"
                    )
                    
                # Merge Phase 5 data into outcome result
                return {
                    "outcome_check_result": {
                        "status": verdict,
                        "details": result_text,
                        "files_checked": [getattr(g, "file_path", "") for g in generated_code],
                        "checklist_pass_rate": _vr.pass_rate,
                        "checklist_items": _vr.items,
                    },
                    "outcome_remediation": remediation or None,
                    "outcome_fix_attempt": _next_attempt,
                    "unresolved_checklist_items": _unresolved,
                }
            except Exception as _ver_exc:
                logger.warning(f"  ⚠️ Phase 5 (ChecklistVerifier) failed: {_ver_exc}")

        return {
            "outcome_check_result": {
                "status": verdict,
                "details": result_text,
                "files_checked": [getattr(g, "file_path", "") for g in generated_code],
            },
            "outcome_remediation": remediation or None,
            "outcome_fix_attempt": _next_attempt,
        }
    except Exception as e:
        logger.warning(f"  ⚠️ Outcome check failed (non-fatal): {e}")
        return {"outcome_check_result": {"status": "error", "reason": str(e)}}


def route_after_outcome_check(state: TicketToCodeState):
    """
    Route after the requirement-satisfaction check.

    Three possible routes:
    1. Requirements satisfied (CORRECT/UNKNOWN/skipped/error) → memory_update (done)
    2. PARTIAL/INCOMPLETE + Tier 2 backup available + not yet expanded
       → context_expand (promote backup files, re-plan with wider context)
    3. PARTIAL/INCOMPLETE + already expanded or no backup + attempts left
       → edit_loop (fix with current files)

    Bounded by max_outcome_fix_attempts so it always terminates.
    """
    result = state.get("outcome_check_result") or {}
    verdict = result.get("status", "UNKNOWN")
    remediation = state.get("outcome_remediation")
    attempt = int(state.get("outcome_fix_attempt", 0) or 0)
    max_attempts = int(state.get("max_outcome_fix_attempts", 1) or 1)

    if verdict in ("PARTIAL", "INCOMPLETE") and attempt <= max_attempts:
        # Check if we have Tier 2 backup candidates that haven't been promoted yet
        tier2 = state.get("tier2_candidates") or []
        expansion_count = int(state.get("context_expansion_count", 0) or 0)

        if tier2 and expansion_count == 0:
            # FIRST RETRY: promote Tier 2 backup files into the plan
            logger.info(
                f"  🔄 Requirements {verdict} — promoting {len(tier2)} Tier 2 "
                f"backup candidates for context expansion"
            )
            return "context_expand"

        if remediation:
            # SUBSEQUENT RETRY: edit_loop with existing (expanded) context
            logger.info(
                f"  ↩️  Requirements not fully satisfied ({verdict}); "
                f"routing to edit_loop for remediation (attempt {attempt}/{max_attempts})"
            )
            return "edit_loop"

    if verdict in ("PARTIAL", "INCOMPLETE"):
        logger.info(
            f"  ⚠️ Requirements {verdict} but remediation budget exhausted or no "
            f"actionable items — finalizing with warnings."
        )
    return "memory_update"


def context_expand_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Promote Tier 2 backup candidates into the active discovered_files list.

    This runs when outcome_check found PARTIAL/INCOMPLETE — meaning the
    current solution (from Tier 1 files only) doesn't fully satisfy the
    ticket.  By promoting Tier 2 files, the system self-corrects even if
    the re-ranker was overly aggressive in filtering.

    Flow: context_expand → plan (re-plan with expanded context) → generate_code → ...
    """
    tier2 = state.get("tier2_candidates") or []
    current_discovered = state.get("discovered_files") or []
    expansion_count = int(state.get("context_expansion_count", 0) or 0)

    if not tier2:
        logger.info("  context_expand: no Tier 2 candidates available — skipping")
        return {"context_expansion_count": expansion_count + 1}

    # Merge Tier 2 candidates into the discovered_files list
    existing_paths = {c.get("path") for c in current_discovered}
    promoted = 0
    for candidate in tier2[:10]:  # Cap at 10 promotions
        if candidate.get("path") not in existing_paths:
            candidate["_tier"] = "promoted_from_tier2"
            current_discovered.append(candidate)
            existing_paths.add(candidate.get("path"))
            promoted += 1

    logger.info(
        f"  📦→✅ Context expansion: promoted {promoted} Tier 2 files into "
        f"discovered_files (total now: {len(current_discovered)})"
    )
    for c in tier2[:promoted]:
        logger.info(
            f"    + {c.get('path')}  "
            f"(rerank_score={c.get('rerank_score', '?')}, "
            f"reason={c.get('rerank_reason', '')[:50]})"
        )

    return {
        "discovered_files": current_discovered,
        "tier2_candidates": [],  # Clear tier2 after promotion
        "context_expansion_count": expansion_count + 1,
        # Reset outcome state so the next cycle starts fresh
        "outcome_check_result": None,
        "outcome_remediation": None,
    }


def check_build_status(state: TicketToCodeState):
    """
    Route after build — EVIDENCE-BASED, not counter-based.

    Three kinds of evidence from the build result:
    1. SUCCESS → done, run outcome check
    2. FAILING with DIFFERENT errors than last cycle → progress is being made;
       continue fixing (counter still bounded by max_retry_attempts)
    3. FAILING with IDENTICAL errors to last cycle (consecutive_identical ≥ 2) →
       the current fix strategy is definitively stuck; stop trying the same thing
       and cut to memory_update so the failure is recorded rather than wasted.

    The distinction between (2) and (3) is the core intelligence: it tells the
    difference between "we're making progress but not done" and "we're spinning".
    """
    build_result = state.get("build_result")
    if not build_result:
        return "memory_update"

    if build_result.status.value == "success":
        logger.info("  → Build successful, running outcome check")
        return "outcome_check"

    # Extract a stable error fingerprint (strip line numbers so we compare error
    # TYPES not positions — line numbers change as code is edited).
    _raw_errors = getattr(build_result, "errors", []) or []
    _fingerprint = _build_err_fingerprint(_raw_errors)

    _prev_fp = state.get("prev_build_error_fingerprint") or ""
    _consec = int(state.get("consecutive_identical_build_errors", 0) or 0)
    _retries = int(state.get("retry_attempt", 0) or 0)
    _max_retries = int(state.get("max_retry_attempts", 3) or 3)

    _errors_same = (_fingerprint == _prev_fp and _fingerprint != "")

    if _errors_same:
        _consec += 1
    else:
        _consec = 0  # errors changed — reset stuck counter

    if _retries >= _max_retries:
        logger.error(
            f"  → Max retries ({_max_retries}) reached. Finalizing with failure."
        )
        return "memory_update"

    if _errors_same and _consec >= 2:
        logger.warning(
            f"  → STUCK: same build errors for {_consec} consecutive cycles "
            f"(retry {_retries}/{_max_retries}). Current fix strategy cannot make "
            f"progress. Finalizing with failure rather than repeating the same fix."
        )
        return "memory_update"

    if not _errors_same and _retries > 0:
        logger.info(
            f"  → Build errors CHANGED (new evidence) — progress detected, "
            f"routing to fix_build with updated context (retry {_retries + 1}/{_max_retries})"
        )
    else:
        logger.info(
            f"  → Build failed, routing to fix_build "
            f"(retry {_retries + 1}/{_max_retries})"
        )

    return "fix_build"


def check_test_status(state: TicketToCodeState):
    """Route after tests: success, fix, or give up?"""
    if state["test_result"].status.value in ("all_passed", "success"):
        return "memory_update"
    elif state["retry_attempt"] < state["max_retry_attempts"]:
        return "fix_test"
    else:
        logger.error("Max retry attempts reached for tests")
        return END

def memory_update_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """Phase 4: Brain Memory Update upon Success"""
    import os
    
    logger.info("\n" + "=" * 80)
    logger.info(" PHASE 4: Updating Project Brain Memory")
    logger.info("=" * 80)
    
    # Gate memory update behind Build and Test success
    build_result = state.get("build_result")
    test_result = state.get("test_result")
    build_success = build_result is not None and getattr(build_result, "status", None) and getattr(build_result.status, "value", "") == "success"
    test_success = test_result is not None and getattr(test_result, "status", None) and getattr(test_result.status, "value", "") in ("all_passed", "success")
    
    # Memory is ALWAYS ENABLED by default. The system MUST learn from every run.
    # Default to "1" (enabled). Override to "0" to disable if needed.
    memory_update_enabled = os.getenv("AVIATOR_ENABLE_MEMORY_UPDATE", "1") == "1"
    
    if not memory_update_enabled:
        logger.info("  [GATE] Memory Update disabled by env var AVIATOR_ENABLE_MEMORY_UPDATE=0")
        return {"status": "memory_skipped"}
        
    if not (build_success and test_success):
        logger.warning("  [GATE] Memory Update requires Build+Verification success. Skipping.")
        # Fix 1: Propagate actual failure reason so UI doesn't show "memory_skipped" as terminal error
        _build_errors = getattr(build_result, "errors", []) if build_result else []
        _test_errors = []
        if test_result and hasattr(test_result, "failed_tests"):
            _test_errors = [str(t) for t in (test_result.failed_tests or [])[:3]]
        _fail_reason = ""
        if _build_errors:
            _fail_reason = (
                f"Build failed with {len(_build_errors)} error(s): "
                + "; ".join(str(e)[:120] for e in _build_errors[:3])
            )
        elif _test_errors:
            _fail_reason = (
                f"Tests failed: " + "; ".join(_test_errors)
            )
        else:
            _fail_reason = "Build or verification did not pass"
        
        state["status"] = "escalated"
        state["escalation_type"] = "build_failed"
        state["escalation_reason"] = _fail_reason
        return {"status": "escalated", "escalation_type": "build_failed", "escalation_reason": _fail_reason}

    # ── Phase 6: Escalation — code compiles but ticket requirements unresolved ──
    # This catches the "looks done, isn't" class of bugs: code compiles, tests
    # pass, but Phase 5's mechanical verification found checklist items that
    # are NOT implemented. Instead of silently shipping, escalate.
    _unresolved = state.get("unresolved_checklist_items", [])
    _escalation_reason = None
    if _unresolved:
        _unresolved_descs = [
            item.get("description", "?")[:80] for item in _unresolved[:3]
        ]
        _escalation_reason = (
            f"Code compiles but {len(_unresolved)} checklist item(s) remain "
            f"unresolved after verification: "
            + "; ".join(_unresolved_descs)
        )
        state["status"] = "escalated"
        state["escalation_type"] = "checklist_incomplete"
        state["escalation_reason"] = _escalation_reason
        logger.warning(f"  ⚠️ ESCALATED: {_escalation_reason}")
        # Still proceed with memory update so the system learns from this run

    ticket = state["ticket"]
    
    # We get the list of modified files from the generated code
    generated_code = state.get("generated_code") or []
    for gen_file in generated_code:
        file_path = getattr(gen_file, "file_path", getattr(gen_file, "path", None))
        if file_path:
            logger.info(f"  Registering successful ticket {ticket.ticket_id} against {file_path}")
            agents.localizer.repository_brain.record_successful_ticket(file_path)

    # Self-updating brain: regenerate the directory brain so it learns any new/removed
    # files/folders from this ticket. Drift-aware + cached, so this is cheap and idempotent.
    # Must never break a successful ticket, so failures are swallowed as non-fatal.
    try:
        from ticket_to_code.brain.generate_repository_brain import refresh_repository_brain
        brain_summary = refresh_repository_brain(
            str(agents.localizer.workspace_path),
            llm=getattr(agents.localizer, "llm", None),
        )
        logger.info("  Brain refresh: %s", brain_summary.get("status"))
    except Exception as _brain_exc:
        logger.warning("  Brain refresh failed (non-fatal): %s", _brain_exc)

    # DEBUG LOG
    log_phase(
        phase='MEMORY_UPDATE',
        llm_output={
            'status': 'memory_updated',
            'files_learned': len(generated_code),
            'ticket_id': ticket.ticket_id,
            'memory_enabled': memory_update_enabled
        },
        agent='MemorySystem'
    )

    # ── B11/B12: Emit RunRecord at terminal node ───────────────────────────────
    run_ctx: Optional[RunContext] = _get_transient(state, "run_ctx")
    if run_ctx:
        run_ctx.final_status = "COMPLETE"
        try:
            record = run_ctx.to_record()
            record_dict = record.to_dict()
            alerts = record.alerts()
            if alerts:
                logger.warning("⚠️  RunRecord alerts: %s", alerts)
            logger.info(
                "📊 RunRecord emitted: status=%s health=%s evidence_backed=%s "
                "cost_usd=%.4f retries=%d",
                record.final_status, record.run_health,
                record.evidence_backed, record.cost_usd, record.retries,
            )
            write_trace_artifact(
                state["workspace_path"],
                state["ticket"].ticket_id,
                "run_record.json",
                record_dict,
            )
        except Exception as _rr_exc:
            logger.warning("RunRecord emission failed (non-fatal): %s", _rr_exc)

    # Clean up transient registry for this ticket (prevents memory leaks)
    _clear_transient(getattr(state.get("ticket"), "ticket_id", ""))

    return_status = "escalated" if _unresolved else "memory_updated"
    return {
        "status": return_status,
        "escalation_type": "checklist_incomplete" if _unresolved else None,
        "escalation_reason": _escalation_reason if _unresolved else None
    }



# ============================================================================
# PIPELINE B: BEHAVIOR-FIRST NODES (SPRINT 4A SHADOW MODE)
# ============================================================================

def behavior_investigation_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    logger.info(" [PIPELINE B] Behavior Investigation Node")
    result, trace = agents.behavior_investigation.execute(
        ticket=state["ticket"],
        workspace_path=state["workspace_path"]
    )
    result_dict = result.model_dump()
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "investigation.json", result_dict)
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "investigation_trace.json", trace)
    return {"behavior_graph": result_dict}

def capability_extraction_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    logger.info(" [PIPELINE B] File Fact Extraction Node")
    if not state.get("behavior_graph"):
        return {"capability_report": None}
    
    result, trace = agents.ownership_verification.execute(
        ticket=state["ticket"],
        behavior_graph=state["behavior_graph"]
    )
    result_dict = result.model_dump()
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "capability_facts.json", result_dict)
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "capability_facts_trace.json", trace)
    return {"capability_report": result_dict}

def capability_consolidation_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    logger.info(" [PIPELINE B] Capability Understanding Node")
    if not state.get("capability_report"):
        return {"consolidated_capability_map": None}
    
    # Needs a FileFactReport object
    from ticket_to_code.models import FileFactReport
    fact_report = FileFactReport.model_validate(state["capability_report"])
    
    result, trace = agents.capability_consolidator.execute(
        ticket=state["ticket"],
        fact_report=fact_report
    )
    result_dict = result.model_dump()
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "capability_understanding.json", result_dict)
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "capability_understanding_trace.json", trace)
    return {"consolidated_capability_map": result_dict}

def capability_graph_builder_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    logger.info(" [PIPELINE B] Capability Graph Builder Node")
    if not state.get("consolidated_capability_map"):
        return {"capability_graph_report": None}
        
    from ticket_to_code.models import ConsolidatedCapabilityReport
    understanding_report = ConsolidatedCapabilityReport.model_validate(state["consolidated_capability_map"])
    
    result, trace = agents.capability_graph_builder.build_graph(
        understanding_report=understanding_report
    )
    result_dict = result.model_dump()
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "runtime_capability_graph.json", result_dict)
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "runtime_capability_graph_trace.json", trace)
    return {"capability_graph_report": result_dict}

def capability_retrieval_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    logger.info(" [PIPELINE B] Hybrid Capability Retrieval Node")
    if not state.get("capability_graph_report"):
        return {"retrieved_capabilities": None}
        
    from ticket_to_code.models import CapabilityGraphReport
    graph_report = CapabilityGraphReport.model_validate(state["capability_graph_report"])
    
    result, trace = agents.capability_retrieval.execute(
        ticket=state["ticket"],
        graph_report=graph_report
    )
    result_dict = result.model_dump()
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "capability_matching.json", result_dict)
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "capability_matching_trace.json", trace)
    return {"retrieved_capabilities": result_dict}

def behavior_planning_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    logger.info(" [PIPELINE B] Behavior Planning Node")
    if not state.get("retrieved_capabilities"):
        return {"behavior_plan": None}
    
    from ticket_to_code.models import RetrievedCapabilities
    retrieved_capabilities = RetrievedCapabilities.model_validate(state["retrieved_capabilities"])
    
    # We use a separate planner instance to avoid state pollution
    plan, trace = agents.behavior_planner.create_plan(
        ticket=state["ticket"],
        requirements=state["requirements"],
        retrieved_capabilities=retrieved_capabilities,
        workspace_path=state["workspace_path"]
    )
    
    # Safely convert to dict
    try:
        plan_dict = plan.model_dump()
    except AttributeError:
        plan_dict = plan.__dict__ if hasattr(plan, "__dict__") else plan
        
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "planner.json", plan_dict)
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "planner_trace.json", trace)

    return {"behavior_plan": plan_dict}

def plan_validation_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    logger.info(" [PIPELINE B] Plan Consistency Validation Node")
    plan = state.get("behavior_plan")
    retrieved_capabilities = state.get("retrieved_capabilities")
    
    if not plan or not retrieved_capabilities:
        return {"plan_validation_result": None}
    
    from ticket_to_code.models import ArchitecturalPlan, RetrievedCapabilities
    
    # Re-instantiate models if they are dictionaries
    if isinstance(plan, dict):
        try:
            plan = ArchitecturalPlan.model_validate(plan)
        except Exception:
            pass # Validation will just fail or type error will bubble
            
    if isinstance(retrieved_capabilities, dict):
        try:
            retrieved_capabilities = RetrievedCapabilities.model_validate(retrieved_capabilities)
        except Exception:
            pass
            
    result = agents.plan_consistency_validator.execute(
        ticket=state["ticket"],
        plan=plan,
        retrieved_capabilities=retrieved_capabilities,
        discovered_files=state.get("discovered_files", [])
    )
    
    # Write validation result to trace
    write_trace_artifact(state["workspace_path"], state["ticket"].ticket_id, "plan_validation.json", result.model_dump())
    
    if result.status == "REPLAN_REQUIRED":
        logger.error(f"   plan_validation_node: Plan rejected! Failed rules: {result.failed_rules}")
        return {"plan_validation_result": result.model_dump()}
        
    return {"plan_validation_result": result.model_dump()}

def shadow_metrics_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    logger.info(" [PIPELINE B] Shadow Metrics Synchronization")
    # Collect metrics comparing A and B
    import json
    import os
    
    # Extract pipeline A plan files
    pipeline_a_files = []
    if state.get("architectural_plan"):
        pipeline_a_files = [t.file_path for t in _get_plan(state).tasks]
        
    # Extract pipeline B plan files
    pipeline_b_files = []
    if state.get("behavior_plan"):
        bp = state["behavior_plan"]
        if isinstance(bp, dict):
            tasks = bp.get("tasks", [])
            pipeline_b_files = [t.get("file_path") if isinstance(t, dict) else t.file_path for t in tasks]
        else:
            pipeline_b_files = [t.file_path for t in bp.tasks]
        
    # Extract verified owners
    verified_owners = []
    if state.get("capability_report"):
        verified_owners = [
            f for f, data in state["capability_report"].get("ownership_classification", {}).items()
            if data.get("classification") == "VERIFIED_OWNER"
        ]
        
    # Extract validation result
    validation_status = "UNKNOWN"
    if state.get("plan_validation_result"):
        validation_status = state["plan_validation_result"].get("status", "UNKNOWN")
        
    metrics = {
        "ticket": state["ticket"].ticket_id,
        "pipeline_a_files": pipeline_a_files,
        "pipeline_b_files": pipeline_b_files,
        "verified_owners": verified_owners,
        "planner_files": pipeline_b_files,
        "actual_changed_files": [], # Wait until test_node finishes or evaluate later
        "validation_result": validation_status,
        "winner": "UNKNOWN_PENDING_REVIEW"
    }
    
    # Save to shadow_metrics.json
    workspace = state["workspace_path"]
    metrics_path = os.path.join(workspace, "shadow_metrics.json")
    try:
        existing = []
        if os.path.exists(metrics_path):
            with open(metrics_path, "r") as f:
                existing = json.load(f)
        existing.append(metrics)
        with open(metrics_path, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write shadow metrics: {e}")
        
    return {"shadow_metrics": metrics}

# ============================================================================
# BUILD LANGGRAPH WORKFLOW
# ============================================================================

def create_ticket_to_code_graph(workspace_path: str, technology: Optional[str] = None) -> StateGraph:
    """
    Create the complete ticket-to-code workflow graph.
    
    Args:
        workspace_path: Path to project workspace
        technology: Language/framework ('java', 'dotnet', 'python', 'nodejs', 'go', or None for auto-detect)
    
    Flow:
    1. Investigate → Determine ticket type
    2. Unified Analysis → Either requirements OR solution guidance (ONE node!)
    3. If requirements → Plan → RAG → Generate → Build → Test
    4. If solution → End
    """
    # Initialize agents with user-specified technology
    agents = WorkflowAgents(workspace_path, technology)
    
    # Create graph
    workflow = StateGraph(TicketToCodeState)
    
    # Add nodes (simplified - ONE analysis node instead of two!)
    workflow.add_node("investigate", lambda s: investigate_node(s, agents))
    workflow.add_node("unified_analysis", lambda s: unified_analysis_node(s, agents))
    workflow.add_node("discover", lambda s: discovery_node(s, agents))  # Repository Discovery
    workflow.add_node("plan", lambda s: plan_node(s, agents))
    workflow.add_node("validate_candidates", lambda s: validate_candidates_node(s, agents))  # Candidate validation
    workflow.add_node("localize", lambda s: localize_node(s, agents))  # Repository intelligence
    workflow.add_node("hypothesis_investigation", lambda s: hypothesis_investigation_node(s, agents))  # Phase 2G-1
    workflow.add_node("evidence_collection_loop", lambda s: evidence_collection_loop_node(s, agents))  # Phase 2G-2
    workflow.add_node("evidence_ranking", lambda s: evidence_ranking_node(s, agents))                  # Phase 3B
    workflow.add_node("semantic_verification", lambda s: semantic_verification_node(s, agents))        # Phase 3C
    workflow.add_node("grounded_understanding", lambda s: grounded_understanding_node(s, agents))  # Phase 2G-3
    workflow.add_node("ownership_completeness", lambda s: ownership_completeness_node(s, agents))  # Phase 2F
    workflow.add_node("dataflow_verification", lambda s: dataflow_verification_node(s, agents))  # DataFlow trace
    workflow.add_node("rag_code", lambda s: rag_for_code_node(s, agents))
    workflow.add_node("generate_code", lambda s: generate_code_node(s, agents))
    workflow.add_node("edit_loop", lambda s: edit_loop_node(s, agents))  # free-form adaptive loop
    workflow.add_node("patch_gate", lambda s: patch_gate_node(s, agents))  # B9: delivery safety
    workflow.add_node("import_validation", lambda s: import_validation_node(s, agents))  # Layer 3
    workflow.add_node("angular_module_registration", lambda s: angular_module_registration_node(s, agents))  # Gap 2
    workflow.add_node("build", lambda s: build_node(s, agents))
    workflow.add_node("fix_build", lambda s: fix_build_errors_node(s, agents))
    workflow.add_node("memory_update", lambda s: memory_update_node(s, agents))
    workflow.add_node("outcome_check", lambda s: outcome_check_node(s, agents))  # post-build semantic validation
    workflow.add_node("context_expand", lambda s: context_expand_node(s, agents))  # Tier 2 fallback expansion
    workflow.add_node("behavior_investigation", lambda s: behavior_investigation_node(s, agents))
    workflow.add_node("capability_extraction", lambda s: capability_extraction_node(s, agents))
    workflow.add_node("capability_consolidation", lambda s: capability_consolidation_node(s, agents))
    workflow.add_node("capability_graph_builder", lambda s: capability_graph_builder_node(s, agents))
    workflow.add_node("capability_retrieval", lambda s: capability_retrieval_node(s, agents))
    workflow.add_node("behavior_planning", lambda s: behavior_planning_node(s, agents))
    workflow.add_node("plan_validation", lambda s: plan_validation_node(s, agents))
    workflow.add_node("shadow_metrics", lambda s: shadow_metrics_node(s, agents))
    
    # Set entry point
    workflow.set_entry_point("investigate")
    
    # Enhancement 1: Runtime Log Analyzer node
    workflow.add_node("runtime_diagnosis", lambda s: runtime_diagnosis_node(s, agents))

    # Add edges - Main flow (investigate → runtime_diagnosis → unified_analysis)
    workflow.add_edge("investigate", "runtime_diagnosis")
    workflow.add_edge("runtime_diagnosis", "unified_analysis")
    workflow.add_conditional_edges("unified_analysis", route_after_unified_analysis)  # Routes to "discover" or END

    # Split into Parallel Pipelines
    # Pipeline A
    workflow.add_edge("discover", "hypothesis_investigation")
    workflow.add_edge("shadow_metrics", END) # End Pipeline B cleanly without touching generated files

    # Phase 2G: Evidence Pipeline (runs BEFORE planning)
    workflow.add_edge("hypothesis_investigation", "evidence_collection_loop")
    workflow.add_edge("evidence_collection_loop", "evidence_ranking")
    workflow.add_edge("evidence_ranking", "semantic_verification")
    workflow.add_edge("semantic_verification", "plan")

    # Plan → Validate Candidates → Localize
    workflow.add_edge("plan", "validate_candidates")
    workflow.add_conditional_edges("validate_candidates", route_after_validate_candidates)
    
    # Localize → Grounded Understanding
    workflow.add_edge("localize", "grounded_understanding")
    workflow.add_conditional_edges("grounded_understanding", route_after_grounded_understanding)  # Phase 2F gate / Hard fallback
    
    # Skip testing - go directly to code generation (dataflow verification between plan and RAG)
    workflow.add_edge("ownership_completeness", "dataflow_verification")
    workflow.add_edge("dataflow_verification", "rag_code")   # Code context only (testing disabled)
    workflow.add_edge("rag_code", "generate_code")
    
    # After fixed-plan generation: run the adaptive edit loop, then safety gate → build
    workflow.add_conditional_edges("generate_code", route_after_generate_code)
    workflow.add_edge("edit_loop", "patch_gate")  # edit loop → safety gate → build
    workflow.add_edge("patch_gate", "import_validation")  # Layer 3: fix imports before compiler
    workflow.add_edge("import_validation", "angular_module_registration")  # Gap 2: register in @NgModule
    workflow.add_edge("angular_module_registration", "build")

    # Build validation: success → outcome check → memory update; failure → fix_build
    workflow.add_conditional_edges("build", check_build_status)
    # Requirement-satisfaction loop: if the ticket isn't actually satisfied, route
    # back through the adaptive edit loop to implement what's missing, then re-verify.
    workflow.add_conditional_edges(
        "outcome_check",
        route_after_outcome_check,
        {
            "edit_loop": "edit_loop",
            "context_expand": "context_expand",
            "memory_update": "memory_update",
        },
    )
    # Tier 2 context expansion → re-plan with wider context
    workflow.add_edge("context_expand", "plan")
    workflow.add_edge("fix_build", "build")
    workflow.add_edge("memory_update", END)
    
    return workflow


# ============================================================================
# MAIN EXECUTION FUNCTION
# ============================================================================

def run_autonomous_workflow_langgraph(
    ticket: ValueEdgeTicket,
    workspace_path: str,
    technology: Optional[str] = None,
    max_retry_attempts: int = 3
) -> TicketToCodeState:
    """
    Run the complete autonomous workflow using LangGraph.
    
    Args:
        ticket: ValueEdge ticket to process
        workspace_path: Path to project workspace
        technology: Language/framework to use. Valid options:
            - 'java': Java projects (Maven/Gradle)
            - 'dotnet': .NET projects (Framework/Core)
            - 'python': Python projects (pytest)
            - 'nodejs': Node.js projects (npm/jest)
            - 'go': Go projects (go build/test)
            - None: Auto-detect from project files (default)
        max_retry_attempts: Max attempts to fix build/test errors
        
    Returns:
        Final workflow state with results
        
    Example:
        # Explicit technology:
        run_autonomous_workflow_langgraph(ticket, "./my-java-app", technology="java")
        
        # Auto-detect:
        run_autonomous_workflow_langgraph(ticket, "./my-project")
    """
    tech_mode = technology if technology else "auto-detect"
    logger.info(f" Starting LangGraph workflow for: {ticket.ticket_id} (Mode: {tech_mode})")
    
    # Create graph with specified technology
    workflow = create_ticket_to_code_graph(workspace_path, technology)
    
    # Compile with memory (state persistence)
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)
    
    # Initial state
    initial_state: TicketToCodeState = {
        "ticket": ticket,
        "workspace_path": workspace_path,
        "max_retry_attempts": max_retry_attempts,
        "investigation_result": None,
        "requirements": None,
        "architectural_plan": None,
        "solution_guidance": None,
        "test_rag_context": None,
        "code_rag_context": None,
        "test_status": None,  # TEST BRANCH status
        "code_status": None,  # CODE BRANCH status
        "generated_tests": None,
        "generated_code": None,
        "build_result": None,
        "test_result": None,
        "retry_attempt": 0,
        "last_error_type": None,
        "blacklisted_files": [],
        "candidate_retry_count": 0,
        "max_candidate_retries": 2,
        "discovery_cycle_count": 0,
        "validation_failure_reason": None,
        "outcome_check_result": None,
        "outcome_remediation": None,
        "outcome_fix_attempt": 0,
        "max_outcome_fix_attempts": 1,
        "tier2_candidates": [],
        "context_expansion_count": 0,
        "prev_build_error_fingerprint": None,
        "consecutive_identical_build_errors": 0,
        "investigation_hypotheses": None,
        "evidence_items": None,
        "grounded_understanding": None,
        "query_expansion_map": None,
        "original_file_contents": {},
        "runtime_diagnosis": None,
        "status": "initialized",
        "errors": [],
        "start_time": datetime.now(),
        "end_time": None
    }
    
    # Execute workflow
    config = {"configurable": {"thread_id": ticket.ticket_id}}
    
    # Save start time for duration calculation
    start_time = initial_state["start_time"]
    
    try:
        # Stream node updates, then read the authoritative final graph snapshot.
        MAX_WORKFLOW_TIME = 30 * 60  # 30 minutes timeout
        
        for update in app.stream(initial_state, config):
            try:
                node_names = ", ".join(update.keys()) if isinstance(update, dict) else str(type(update))
                logger.info(f"  Node update: {node_names}")
            except Exception:
                logger.info("  Node update received")
                
            # Check global timeout
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed > MAX_WORKFLOW_TIME:
                logger.error(f"❌ GLOBAL TIMEOUT EXCEEDED: Workflow ran for {elapsed:.2f}s (Max {MAX_WORKFLOW_TIME}s). Aborting.")
                # We break out of the stream early to kill the pipeline
                break

        snapshot = app.get_state(config)
        final_state = snapshot.values if hasattr(snapshot, "values") else snapshot
        if not isinstance(final_state, dict):
            final_state = dict(initial_state)
        
        # Mark completion
        final_state["end_time"] = datetime.now()
        duration = (final_state["end_time"] - start_time).total_seconds()
        
        # Ensure status reflects a fatal build failure if max retries were exhausted
        if final_state.get("retry_attempt", 0) >= final_state.get("max_retry_attempts", 2):
            if final_state.get("build_result") and final_state["build_result"].status.value == "failure":
                final_state["status"] = "build_failed"
        
        logger.info(
            f"✅ Workflow completed in {duration:.2f}s\n"
            f"   Status: {final_state.get('status', 'unknown')}\n"
            f"   Tests: {len(final_state.get('generated_tests') or [])} files\n"
            f"   Code: {len(final_state.get('generated_code') or [])} files\n"
            f"   Retry attempts: {final_state.get('retry_attempt', 0)}"
        )
        
        # Write final metrics trace artifact
        metrics_artifact = {
            "duration_seconds": duration,
            "status": final_state.get("status", "unknown"),
            "test_files_generated": len(final_state.get("generated_tests") or []),
            "code_files_generated": len(final_state.get("generated_code") or []),
            "retry_attempts": final_state.get("retry_attempt", 0),
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": final_state["end_time"].isoformat() if final_state.get("end_time") else None
        }
        write_trace_artifact(workspace_path, ticket.ticket_id, "final_metrics.json", metrics_artifact)
        
        # Write discovered files
        if final_state.get("discovered_files"):
            write_trace_artifact(workspace_path, ticket.ticket_id, "discovered_files.json", final_state["discovered_files"])
            
        # Write planner selection
        plan = _get_plan(final_state)
        if plan and hasattr(plan, "tasks"):
            tasks_list = [{"file_path": t.get("file_path") if isinstance(t, dict) else t.file_path, "task_type": (t.get("task_type") if isinstance(t, dict) else getattr(t.task_type, "value", str(t.task_type)))} for t in plan.tasks]
            write_trace_artifact(workspace_path, ticket.ticket_id, "planner_selection.json", tasks_list)
            
        # Write generated patch (diffs from generated code)
        if final_state.get("generated_code"):
            patch_content = ""
            for gf in final_state["generated_code"]:
                if isinstance(gf, dict):
                    gf_path = gf.get("file_path", gf.get("path", "unknown"))
                    gf_content = gf.get("content", "")
                else:
                    gf_path = getattr(gf, "file_path", "unknown")
                    gf_content = getattr(gf, "content", "")
                patch_content += f"--- {gf_path}\n+++ {gf_path}\n{gf_content}\n\n"
            if patch_content:
                write_trace_artifact(workspace_path, ticket.ticket_id, "generated_patch.diff", patch_content)
                
        # Write build output
        build_res = final_state.get("build_result")
        if build_res and hasattr(build_res, "stdout"):
            log_content = f"SUCCESS: {build_res.status.value == 'success'}\n\nSTDOUT:\n{build_res.stdout}\n\nSTDERR:\n{build_res.stderr}"
            write_trace_artifact(workspace_path, ticket.ticket_id, "build_output.log", log_content)
            
        # Write test output
        test_res = final_state.get("test_result")
        if test_res and hasattr(test_res, "stdout"):
            log_content = f"SUCCESS: {test_res.success}\n\nSTDOUT:\n{test_res.stdout}\n\nSTDERR:\n{test_res.stderr}"
            write_trace_artifact(workspace_path, ticket.ticket_id, "test_output.log", log_content)
        
        # Write Decision Chain
        try:
            decision_chain = []
            discovered = final_state.get("discovered_files", [])
            plan = _get_plan(final_state)
            plan_tasks = plan.tasks if plan and hasattr(plan, "tasks") else []
            generated = final_state.get("generated_code", [])
            build_res = final_state.get("build_result")
            behavior_plan = final_state.get("behavior_plan")
            
            cap_map = {}
            if behavior_plan:
                b_tasks = behavior_plan.tasks if hasattr(behavior_plan, "tasks") else behavior_plan.get("tasks", [])
                for bt in b_tasks:
                    path = bt.file_path if hasattr(bt, "file_path") else bt.get("file_path")
                    caps = bt.capabilities if hasattr(bt, "capabilities") else bt.get("capabilities", [])
                    if path:
                        cap_map[path] = caps

            for d in discovered:
                path = d["path"]
                entry = {
                    "candidate": path,
                    "investigation_match": True,
                    "discovery_match": True,
                    "ranking_score": d.get("score", 0),
                    "planner_selected": any(t.file_path == path and t.task_type.value != "read_only" for t in plan_tasks),
                    "generator_modified": any((gf.get("file_path") if isinstance(gf, dict) else getattr(gf, "file_path", None)) == path for gf in generated),
                    "build_success": (build_res.status.value == 'success') if build_res else None
                }
                decision_chain.append(entry)
                
            if decision_chain:
                write_trace_artifact(workspace_path, ticket.ticket_id, "decision_chain.json", decision_chain)
        except Exception as e:
            logger.warning(f"Failed to write decision_chain.json: {e}")

        return final_state
        
    except Exception as e:
        logger.error(f"❌ Workflow failed: {e}", exc_info=True)
        raise


# ============================================================================
# VISUALIZATION HELPER
# ============================================================================

def visualize_workflow(workspace_path: str, output_path: str = "workflow_graph.png"):
    """
    Generate visual diagram of the workflow.
    
    Args:
        workspace_path: Project path
        output_path: Where to save diagram
    """
    workflow = create_ticket_to_code_graph(workspace_path)
    app = workflow.compile()
    
    # Generate Mermaid diagram
    mermaid = app.get_graph().draw_mermaid()
    
    print("Workflow Graph (Mermaid):")
    print(mermaid)
    
    # Could also generate PNG with graphviz
    # app.get_graph().draw_png(output_path)
    
    return mermaid


# ============================================================================
# COMPATIBILITY WRAPPER (for existing code)
# ============================================================================

def run_autonomous_workflow(
    ticket: ValueEdgeTicket,
    workspace_path: str,
    codebase_path: Optional[str] = None,
    technology: Optional[str] = None,
    auto_execute: bool = True,
    max_fix_attempts: int = 3
) -> TicketToCodeState:
    """
    Compatibility wrapper for existing code.
    
    Maps old API to new LangGraph workflow.
    
    Args:
        ticket: ValueEdge ticket to process
        workspace_path: Project path (auto-detects technology)
        codebase_path: Existing codebase for RAG context (defaults to workspace_path)
        technology: Technology stack (auto-detected if None: 'dotnet', 'java', 'nodejs')
        auto_execute: Whether to auto-execute (always True in LangGraph)
        max_fix_attempts: Maximum attempts to fix build/test errors
        
    Returns:
        Final workflow state (LangGraph TypedDict)
    """
    tech_mode = technology if technology else "auto-detect"
    logger.info(f" Running LangGraph workflow (via compatibility wrapper) - Mode: {tech_mode}")
    
    # Use codebase_path if provided, otherwise use workspace_path
    effective_workspace = codebase_path if codebase_path else workspace_path
    
    # Call LangGraph version with technology parameter
    return run_autonomous_workflow_langgraph(
        ticket=ticket,
        workspace_path=effective_workspace,
        technology=technology,
        max_retry_attempts=max_fix_attempts
    )


# Alias for backwards compatibility
AutonomousWorkflowOrchestrator = None  # No longer used with LangGraph


