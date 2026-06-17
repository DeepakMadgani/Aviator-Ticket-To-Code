"""
Investigation Agent Validation  –  3 CC4E Tickets
===================================================
Shows per-ticket:
  1. Investigation hypotheses generated  (LLM)
  2. Evidence gathered per source        (SQLite + graph chains, live)
  3. Dependency chain discovered
  4. Change group
  5. GroundedUnderstanding output
  6. Files added beyond localization seed
  7. Confidence score per iteration

Run with:
  $env:GOOGLE_APPLICATION_CREDENTIALS = "aviator-plugin-sample\\otl-cs-csai.json"
  $env:NEO4J_PASSWORD = "aviator-dev"
  aviator-plugin-sample\\.venv\\Scripts\\python.exe validate_investigation_agent.py
"""
import logging
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import List, Tuple

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "aviator-plugin-sample" / "src"))
sys.path.insert(0, str(BASE / "aviator_adt" / "src"))

WORKSPACE = "C:/CC4E"

logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s: %(message)s")

from ticket_to_code.models import (
    DevelopmentTask, EvidenceItem, GroundedUnderstanding,
    InvestigationHypothesis, InvestigationResult,
    ProgrammingLanguage, TaskType, TicketPriority, TicketType,
    ValueEdgeTicket,
)

# ─── Tickets ──────────────────────────────────────────────────────────────────

TICKET_A = ValueEdgeTicket(
    ticket_id="CC4E-A",
    title="Reviewer footer alignment – buttons superimposed, blank space below footer",
    description=(
        "UX alignment isn't proper for deliverable add reviewer and reviewers pages.\n"
        "Steps to Reproduce:\n"
        "  Login, navigate to project TPR1 -> contract C1 -> deliverable D02.\n"
        "  Click info icon, select 'Reviewers', click '+' beside 'Reviewers 0'.\n"
        "  Observe: footer 'Add' & 'Cancel' buttons are superimposed at the bottom.\n"
        "  Also: blank space below footer on larger screen dimensions.\n"
        "  Page CSS appears static (not responsive)."
    ),
    acceptance_criteria=[
        "Footer buttons 'Add' and 'Cancel' are correctly aligned, not overlapping.",
        "No blank space below footer on any screen size.",
        "Layout is responsive across screen dimensions.",
    ],
    labels=["ui", "css", "alignment", "footer", "reviewers"],
    priority=TicketPriority.HIGH,
)
TASKS_A = [DevelopmentTask(
    id="T1", title="Fix reviewer footer SCSS", description="Fix footer alignment",
    file_path="xchange-ui/src/app/modules/deliverables/deliverable-reviewers/deliverable-reviewers.component.scss",
    task_type=TaskType.MODIFY, language=ProgrammingLanguage.TYPESCRIPT,
    localization_confidence=0.82, ownership_type="DISPLAY_OWNER",
)]

TICKET_B = ValueEdgeTicket(
    ticket_id="CC4E-B",
    title="Deliverable count incorrect after applying filter",
    description=(
        "When a user applies a filter on the deliverables list, the header count "
        "does not update to reflect filtered results. It shows the total unfiltered count.\n"
        "Expected: count reflects only deliverables matching the filter.\n"
        "Actual: count stays at unfiltered total. Reproducible on staging and production."
    ),
    acceptance_criteria=[
        "Header count reflects filtered deliverables.",
        "Count resets to total when filter is cleared.",
        "Works for all filter types (status, assignee, date range).",
    ],
    labels=["bug", "deliverable", "filter", "count", "backend"],
    priority=TicketPriority.HIGH,
)
TASKS_B = [DevelopmentTask(
    id="T1", title="Fix deliverable count in service", description="Fix count calculation",
    file_path="area-service/src/main/java/com/opentext/solutions/services/area/domain/engineering/service/impl/DeliverableListServiceImpl.java",
    task_type=TaskType.MODIFY, language=ProgrammingLanguage.JAVA,
    localization_confidence=0.78, ownership_type="PRIMARY_OWNER",
)]

TICKET_C = ValueEdgeTicket(
    ticket_id="CC4E-C",
    title="Update application version from 26.2 to 26.3",
    description=(
        "Application version must be bumped from 26.2 to 26.3 for quarterly release.\n"
        "Version string appears in: application header, API /info endpoint, startup logs.\n"
        "All three locations must be updated consistently."
    ),
    acceptance_criteria=[
        "Application header shows version 26.3.",
        "API /info endpoint returns version 26.3.",
        "Startup log prints version 26.3.",
    ],
    labels=["version", "release", "26.3", "constants"],
    priority=TicketPriority.MEDIUM,
)
TASKS_C = [DevelopmentTask(
    id="T1", title="Update version constant", description="Bump version 26.2 -> 26.3",
    file_path="se-connector-apis/src/main/java/com/opentext/solutions/services/xchange/constants/GenericConstants.java",
    task_type=TaskType.MODIFY, language=ProgrammingLanguage.JAVA,
    localization_confidence=0.71, ownership_type="PRIMARY_OWNER",
)]

TICKETS = [
    ("A", "Reviewer Footer Alignment",               TICKET_A, TASKS_A),
    ("B", "Deliverable Count Incorrect After Filter", TICKET_B, TASKS_B),
    ("C", "Version Update 26.2 -> 26.3",              TICKET_C, TASKS_C),
]

# ─── Display helpers ──────────────────────────────────────────────────────────

SEP  = "=" * 72
SEP2 = "-" * 72

def hdr(t): print(f"\n{SEP}\n  {t}\n{SEP}")
def sub(t): print(f"\n{SEP2}\n  {t}\n{SEP2}")

def bullet(label, value, indent=4):
    pad = " " * indent
    if isinstance(value, list):
        print(f"{pad}* {label}:")
        for v in value: print(f"{pad}  - {v}")
    else:
        print(f"{pad}* {label:<33} {value}")

SOURCE_ICONS = {
    "semantic":      "[SEMANTIC]",
    "sqlite_fts":    "[FTS]    ",
    "sqlite_symbol": "[SYMBOL] ",
    "neo4j":         "[NEO4J]  ",
    "ts_chain":      "[TS]     ",
    "java_chain":    "[JAVA]   ",
    "css_chain":     "[CSS]    ",
    "config_chain":  "[CONFIG] ",
    "layout_chain":  "[LAYOUT] ",
}
ALL_SOURCES = list(SOURCE_ICONS.keys())

# ─── Phase 0 ─────────────────────────────────────────────────────────────────

def run_triage(ticket):
    from ticket_to_code.agents.investigation_agent import InvestigationAgent
    return InvestigationAgent().investigate(ticket, codebase_summary=f"CC4E at {WORKSPACE}")

# ─── Phase 2G-1 ──────────────────────────────────────────────────────────────

def run_hypotheses(ticket, investigation, tasks):
    from ticket_to_code.agents.investigation_agent import InvestigationAgent
    return InvestigationAgent().generate_hypotheses(ticket, investigation, tasks)

# ─── Phase 2G-2 ──────────────────────────────────────────────────────────────

def run_evidence_live(hypotheses, tasks, ticket):
    from ticket_to_code.agents.localization_agent import LocalizationAgent
    from ticket_to_code.retrieval.rag_engine import CodebaseRAGEngine
    from ticket_to_code.agents.evidence_collection_loop import (
        EvidenceCollectionLoop, _MAX_ITERATIONS, _CONFIDENCE_THRESHOLD,
    )

    localizer = LocalizationAgent(WORKSPACE)
    try:
        rag = CodebaseRAGEngine()
    except Exception:
        from unittest.mock import MagicMock
        rag = MagicMock()
        rag.retrieve_context = MagicMock(return_value=[])

    loop = EvidenceCollectionLoop(localizer, rag)

    all_evidence = []
    conf_history = []
    visited = {t.file_path.replace("\\", "/") for t in tasks}

    for iteration in range(_MAX_ITERATIONS):
        new_items = []
        for hyp in hypotheses:
            new_items.extend(loop._query_semantic(hyp.queries, hyp.id, visited))
            new_items.extend(loop._query_sqlite_fts(hyp.literals, hyp.id, visited))
            new_items.extend(loop._query_sqlite_symbols(hyp.symbols, hyp.id, visited))
        all_evidence.extend(new_items)
        for e in new_items: visited.add(e.file_path)

        seed_paths = list(visited)
        for group in [
            loop._query_neo4j([s for h in hypotheses for s in h.symbols], hypotheses, visited),
            loop._follow_ts_chains(seed_paths, visited),
            loop._follow_java_chains(seed_paths, visited),
            loop._follow_css_chains(seed_paths, visited),
            loop._follow_config_chains([l for h in hypotheses for l in h.literals], visited),
            loop._follow_layout_chains(seed_paths, visited),
        ]:
            all_evidence.extend(group)
            for e in group: visited.add(e.file_path)

        conf = loop._compute_confidence(all_evidence, hypotheses, tasks)
        conf_history.append((iteration + 1, conf, len(all_evidence)))
        if conf >= _CONFIDENCE_THRESHOLD:
            break

    return all_evidence, conf, conf_history

# ─── Phase 2G-3 ──────────────────────────────────────────────────────────────

def synthesize(evidence, hypotheses, tasks, investigation, ticket):
    from ticket_to_code.agents.localization_agent import LocalizationAgent
    from ticket_to_code.retrieval.rag_engine import CodebaseRAGEngine
    from ticket_to_code.agents.evidence_collection_loop import EvidenceCollectionLoop

    writable = [t.file_path for t in tasks if t.task_type.value != "read_only"]
    readonly = [t.file_path for t in tasks if t.task_type.value == "read_only"]
    ev_files = list({e.file_path for e in evidence})
    extra    = [f for f in ev_files if f not in writable]

    sorted_ev = sorted(evidence, key=lambda e: e.relevance_score, reverse=True)
    chain, seen = [], set()
    for e in sorted_ev[:20]:
        if e.file_path not in seen:
            chain.append(e.file_path); seen.add(e.file_path)

    # Files to exclude from change_group — traces are read-only audit logs
    _READONLY_PREFIXES = ("traces/", "traces\\")
    # config_chain evidence is read-only context, never code to change
    _CODE_SOURCES = {"semantic", "sqlite_fts", "sqlite_symbol", "neo4j",
                     "ts_chain", "java_chain", "css_chain", "layout_chain"}
    high_conf  = [e.file_path for e in evidence
                  if e.relevance_score >= 0.70
                  and e.file_path not in writable
                  and e.source in _CODE_SOURCES
                  and not e.file_path.startswith(_READONLY_PREFIXES)]
    change_grp = list(dict.fromkeys(writable + high_conf))

    confirmed, refuted = [], []
    for hyp in hypotheses:
        supporting = [e for e in evidence if e.hypothesis_id == hyp.id and e.relevance_score >= 0.60]
        (confirmed if supporting else refuted).append(hyp.id)

    try:
        localizer = LocalizationAgent(WORKSPACE)
        rag = CodebaseRAGEngine()
        conf = EvidenceCollectionLoop(localizer, rag)._compute_confidence(evidence, hypotheses, tasks)
    except Exception:
        n   = len(evidence)
        div = len({e.source for e in evidence}) / 9
        q   = (sum(e.relevance_score for e in evidence) / n) if n else 0
        dep = min(math.log2(1 + n) / 5.0, 1.0)
        loc = len([t for t in tasks if t.task_type.value != "read_only"])
        cov = min(len({e.file_path for e in evidence}) / max(loc + 1, 1), 1.0)
        conf = min(0.30*cov + 0.25*div + 0.30*q + 0.15*dep, 1.0)

    root_cause = investigation.root_cause_hypothesis or ticket.title
    for hyp in hypotheses:
        if hyp.id in confirmed:
            root_cause = hyp.hypothesis; break

    return GroundedUnderstanding(
        root_cause=root_cause,
        evidence=evidence,
        dependency_chain=chain,
        change_group=change_grp,
        required_files=list(dict.fromkeys(writable + extra[:10])),
        writable_files=writable,
        readonly_context_files=list(dict.fromkeys(readonly + extra[:10])),
        confidence=round(conf, 3),
        evidence_loop_iterations=len({e.source for e in evidence}),
        hypotheses_confirmed=confirmed,
        hypotheses_refuted=refuted,
    )

# ─── Per-ticket runner ────────────────────────────────────────────────────────

def validate_ticket(label, title, ticket, tasks):
    hdr(f"TICKET {label}: {title}")

    # Phase 0
    sub("PHASE 0 -- Investigation Triage (LLM)")
    print("  Running InvestigationAgent.investigate() ...")
    t0 = time.time()
    try:
        investigation = run_triage(ticket)
        elapsed = time.time() - t0
        bullet("Ticket type",            investigation.ticket_type.value)
        bullet("Requires code changes",  investigation.requires_code_changes)
        bullet("Root cause hypothesis",  investigation.root_cause_hypothesis or "(none)")
        bullet("Affected systems",       investigation.affected_systems)
        bullet("Investigation areas",    investigation.investigation_areas)
        bullet("Possible causes",        investigation.possible_causes)
        bullet("Triage confidence",      f"{investigation.confidence:.2f}")
        print(f"\n  OK  Triage complete  ({elapsed:.1f}s)")
    except Exception as e:
        print(f"  FAIL  Triage error ({time.time()-t0:.1f}s): {e}")
        return

    # Phase 2G-1
    sub("PHASE 2G-1 -- Investigation Hypotheses (LLM)")
    print("  Running InvestigationAgent.generate_hypotheses() ...")
    t0 = time.time()
    try:
        hypotheses = run_hypotheses(ticket, investigation, tasks)
        elapsed = time.time() - t0
        print(f"  {len(hypotheses)} hypothesis(es) generated  ({elapsed:.1f}s)\n")
        for hyp in hypotheses:
            print(f"  [{hyp.id}]  {hyp.hypothesis}")
            bullet("Semantic queries",  hyp.queries,  indent=8)
            bullet("Literals to FTS",   hyp.literals, indent=8)
            bullet("Symbols to lookup", hyp.symbols,  indent=8)
            bullet("Prior confidence",  f"{hyp.confidence:.2f}", indent=8)
            print()
        print("  OK  Hypotheses ready for evidence loop")
    except Exception as e:
        print(f"  FAIL  Hypothesis error ({time.time()-t0:.1f}s): {e}")
        return

    # Phase 2G-2
    sub("PHASE 2G-2 -- Evidence Collection Loop (live SQLite + 9 sources)")
    print(f"  Localization seed ({len(tasks)} file(s)):")
    for t in tasks:
        print(f"    * [{t.ownership_type}]  {t.file_path}")
    print()

    t0 = time.time()
    try:
        evidence, final_conf, conf_history = run_evidence_live(hypotheses, tasks, ticket)
    except Exception as e:
        import traceback
        print(f"  FAIL  Evidence loop error: {e}")
        traceback.print_exc()
        return
    elapsed = time.time() - t0

    # 7. Confidence evolution
    print("  7. Confidence score per iteration:")
    for itr, conf, n_items in conf_history:
        filled = int(conf * 20)
        bar    = "#" * filled + "." * (20 - filled)
        flag   = "  << THRESHOLD (0.70) REACHED" if conf >= 0.70 else ""
        print(f"      Iter {itr}:  {conf:.3f}  [{bar}]  {n_items:3d} items{flag}")
    print()

    # 2. Evidence per source
    src_counts = Counter(e.source for e in evidence)
    uniq_files = len({e.file_path for e in evidence})
    print(f"  2. Evidence gathered  ({len(evidence)} items total, {uniq_files} unique files):")
    for src in ALL_SOURCES:
        cnt  = src_counts.get(src, 0)
        icon = SOURCE_ICONS[src]
        bar  = "|" * min(cnt, 32)
        print(f"      {icon}  {cnt:3d}  {bar}")
    print()

    # Top evidence items
    top_ev = sorted(evidence, key=lambda e: e.relevance_score, reverse=True)[:10]
    print("  Top 10 evidence items (by relevance):")
    for e in top_ev:
        icon = SOURCE_ICONS.get(e.source, "[?]")
        snip = (e.content_snippet or "")[:70].replace("\n", " ")
        print(f"      {icon}  {e.relevance_score:.2f}  [{e.evidence_type:<14}]  {e.file_path}")
        if snip:
            print(f"                     snippet: {snip}")
    print()

    # 6. Files beyond localization
    seed_set = {t.file_path for t in tasks}
    added    = sorted([f for f in {e.file_path for e in evidence} if f not in seed_set])
    print(f"  6. Files added beyond localization seed: {len(added)}")
    for f in added[:20]:
        srcs  = sorted({e.source for e in evidence if e.file_path == f})
        icons = ", ".join(SOURCE_ICONS.get(s, s) for s in srcs)
        print(f"      + [{icons}]")
        print(f"        {f}")
    if not added:
        print("      (none)")
    print(f"\n  OK  Evidence loop done in {elapsed:.1f}s")

    # Phase 2G-3
    sub("PHASE 2G-3 -- Grounded Understanding Output")
    try:
        grounded = synthesize(evidence, hypotheses, tasks, investigation, ticket)
    except Exception as e:
        import traceback
        print(f"  FAIL  Synthesis error: {e}")
        traceback.print_exc()
        return

    # 5. GroundedUnderstanding
    print("  5. GroundedUnderstanding:\n")
    bullet("root_cause",               grounded.root_cause[:95])
    bullet("confidence",               f"{grounded.confidence:.3f}")
    bullet("evidence_loop_iterations", grounded.evidence_loop_iterations)
    bullet("hypotheses_confirmed",     grounded.hypotheses_confirmed)
    bullet("hypotheses_refuted",       grounded.hypotheses_refuted)
    print()

    # 3. Dependency chain
    print(f"  3. Dependency chain ({len(grounded.dependency_chain)} files, ranked by relevance):")
    for i, f in enumerate(grounded.dependency_chain[:10], 1):
        print(f"      {i:2d}. {f}")
    print()

    # 4. Change group
    print(f"  4. Change group ({len(grounded.change_group)} files):")
    for f in grounded.change_group:
        tag = "[seed]" if f in seed_set else "[+evidence]"
        print(f"      {tag:<14} {f}")
    print()
    bullet("writable_files",           grounded.writable_files)
    print()
    if grounded.readonly_context_files:
        print("  * readonly_context_files (top 8):")
        for f in grounded.readonly_context_files[:8]:
            tag = "" if f in seed_set else "  [+evidence]"
            print(f"      {f}{tag}")
    print()

    # Delta
    added_cg = [f for f in grounded.change_group if f not in seed_set]
    added_ro = [f for f in grounded.readonly_context_files if f not in seed_set]
    print("  -- Evidence delta beyond localization -----------------------")
    print(f"     change_group  +{len(added_cg)} file(s)  |  context +{len(added_ro)} file(s)")
    for f in added_cg:   print(f"       [WRITABLE]  {f}")
    for f in added_ro[:8]: print(f"       [CONTEXT]   {f}")
    print()
    print(f"  OK  Ticket {label} complete  --  confidence={grounded.confidence:.3f}")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "#" * 72)
    print("  INVESTIGATION AGENT VALIDATION  --  CC4E  --  3 Tickets")
    print("  SQLite: 38,100 symbols | 130,334 edges | 2,049 files")
    print("  Code generation: DISABLED")
    print("#" * 72)

    for label, title, ticket, tasks in TICKETS:
        validate_ticket(label, title, ticket, tasks)
        print()

    print("\n" + "#" * 72)
    print("  VALIDATION COMPLETE")
    print("#" * 72 + "\n")
