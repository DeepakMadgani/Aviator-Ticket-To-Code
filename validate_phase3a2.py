"""
Phase 3A.2 validation: simulate change_group promotion for the version ticket.
Confirm run-job.sh and app.component.ts become promoted writable tasks.
"""
import json
from pathlib import Path
from collections import defaultdict

WORKSPACE = Path(r"C:\CC4E")
RS_FILE   = WORKSPACE / ".aviator" / "repository_search.json"

# ── Evidence items from repository_search.json (deduplicated) ────────────────
rs_data = json.loads(RS_FILE.read_text(encoding="utf-8"))
# Use only the first batch (post-fix = single flush = non-duplicated)
seen = set()
evidence = []
for r in rs_data:
    key = (r["file_path"], r["query"], r["line_number"])
    if key not in seen:
        seen.add(key)
        evidence.append({
            "file_path": r["file_path"],
            "relevance_score": r["confidence"],
            "source": "repository_search",
        })

# ── Simulate writable_from_plan (known from trace 07_localization.json) ───────
writable_from_plan = [
    "xchange-ui/src/jato/header/jheader.component.ts",
    "xchange-ui/src/jato/dashboard/jdashboard.component.ts",
]

_CODE_SOURCES = {
    "semantic", "sqlite_fts", "sqlite_symbol", "neo4j",
    "ts_chain", "java_chain", "css_chain", "layout_chain",
    "repository_search",
}

# ── Compute high_conf_evidence (mirrors grounded_understanding_node) ──────────
high_conf_evidence = list(dict.fromkeys(
    e["file_path"] for e in evidence
    if e["relevance_score"] >= 0.70
    and e["file_path"] not in writable_from_plan
    and e["source"] in _CODE_SOURCES
    and not e["file_path"].startswith(("traces/", "traces\\"))
))

print(f"high_conf_evidence candidates: {len(high_conf_evidence)}")

# ── Simulate promotion (disk existence check) ─────────────────────────────────
# Sort by best score descending (mirrors implementation)
def _best_score(fp):
    return max((e["relevance_score"] for e in evidence if e["file_path"] == fp), default=0.70)
sorted_evidence_files = sorted(high_conf_evidence, key=_best_score, reverse=True)

promoted = []
for fp in sorted_evidence_files:
    full = WORKSPACE / fp
    exists = full.exists()
    best_score = max(
        (e["relevance_score"] for e in evidence if e["file_path"] == fp),
        default=0.70,
    )
    best_source = next(
        (e["source"] for e in sorted(evidence, key=lambda e: e["relevance_score"], reverse=True)
         if e["file_path"] == fp),
        "repository_search",
    )
    if exists:
        promoted.append({
            "file_path": fp,
            "task_id": f"grp-{len(promoted)+1}",
            "score": best_score,
            "source": best_source,
            "language": Path(fp).suffix,
        })
    else:
        print(f"  SKIP (not on disk): {fp}")

print(f"\nPromoted tasks: {len(promoted)}")
print()

# ── Check target files ────────────────────────────────────────────────────────
targets = ["run-job.sh", "app.component.ts"]
for target in targets:
    match = next((p for p in promoted if target in p["file_path"]), None)
    if match:
        print(f"  ✅ PRESENT  {match['file_path']}")
        print(f"       task_id={match['task_id']}  score={match['score']}  src={match['source']}  lang={match['language']}")
    else:
        print(f"  ❌ ABSENT   {target}")

print()
print("Full promoted list:")
for p in promoted:
    print(f"  [{p['task_id']}] {p['score']:.2f} {p['source']:20s} {p['file_path']}")
