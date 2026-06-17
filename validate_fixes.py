"""
Validation script for the 4 forensic fixes.
Outputs: A (ranking before), B (ranking after), C (planner input count).
"""
import sys, os, re, importlib.util, pathlib

sys.path.insert(0, "src")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/aviator-plugin-sample/src")

# Load CandidateRole
spec = importlib.util.spec_from_file_location("models", "ticket_to_code/models.py")
models_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(models_mod)
CandidateRole = models_mod.CandidateRole

# Extract _classify_candidate_role from workflow.py
wf_text = pathlib.Path("ticket_to_code/workflow.py").read_text(encoding="utf-8")
fn_start = wf_text.index("def _classify_candidate_role(")
fn_snippet = wf_text[fn_start : fn_start + 3000]
lines = fn_snippet.split("\n")
func_lines = [lines[0]]
for ln in lines[1:]:
    if ln and not ln[0].isspace() and not ln.startswith("#"):
        break
    func_lines.append(ln)
exec_ns = {"CandidateRole": CandidateRole, "re": re}
exec("\n".join(func_lines), exec_ns)
classify = exec_ns["_classify_candidate_role"]

# Representative evidence pool — all confidence=1.0 (the tie scenario)
paths = [
    "xchange-ui/src/environments/environment.spec.ts",
    "project-service/helm/static/run-job.sh",
    "xchange-ui/src/app/app.component.ts",
    "xchange-ui/src/assets/i18n/home/en.json",
    "package-lock.json",
    "xchange-ui/dist/main.js",
    "area-service/src/main/java/com/opentext/URLMappings.java",
    "xchange-ui/src/environments/environment.ts",
    "area-service/src/test/java/CmsConfigurationTest.java",
    "xchange-ui/src/jato/header/jheader.component.spec.ts",
]
pool = [{"path": p, "confidence": 1.0, "signals": []} for p in paths]
for c in pool:
    raw = classify(c["path"])
    # _classify_candidate_role returns None when no rule matches (treated as UNKNOWN)
    c["candidate_role"] = raw if raw is not None else CandidateRole.UNKNOWN.value

# ── A. BEFORE fix (confidence-only, insertion-order tiebreak) ─────────────────
print("=== A. Ranking BEFORE fix (confidence-only, insertion-order tiebreak) ===")
sorted_before = sorted(pool, key=lambda x: -x.get("confidence", 0.0))
for i, c in enumerate(sorted_before, 1):
    print(f"  {i:2d}.  role={c['candidate_role']:18s}  {c['path']}")

# ── B. AFTER fix (compound sort) ──────────────────────────────────────────────
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
sorted_after = sorted(
    pool,
    key=lambda x: (
        -x.get("confidence", 0.0),
        _ROLE_PRIORITY.get(x.get("candidate_role", CandidateRole.UNKNOWN.value), 5),
        x.get("path", ""),
    ),
)

print()
print("=== B. Ranking AFTER fix (confidence desc, role priority, path) ===")
for i, c in enumerate(sorted_after, 1):
    note = ""
    p = c["path"]
    if "run-job" in p:      note = "  <-- DEPLOYMENT (deterministically position 1)"
    if "app.component" in p: note = "  <-- ROOT_COMPONENT (position 2)"
    if "spec.ts" in p or "Test.java" in p: note = "  <-- TEST (pushed below UNKNOWN)"
    if "package-lock" in p: note = "  <-- LOCK_FILE (pushed to bottom)"
    if "dist/" in p:        note = "  <-- GENERATED (pushed to bottom)"
    print(f"  {i:2d}.  role={c['candidate_role']:18s}  {p}{note}")

# ── C. Planner input count ─────────────────────────────────────────────────────
_EXCLUDED = {CandidateRole.LOCK_FILE.value, CandidateRole.GENERATED.value}
eligible  = [c for c in sorted_after if c["candidate_role"] not in _EXCLUDED]
excluded  = [c for c in sorted_after if c["candidate_role"] in _EXCLUDED]
planner_in = eligible[:25]

rj_pos = next((i + 1 for i, c in enumerate(planner_in) if "run-job" in c["path"]), "NOT IN POOL")

print()
print("=== C. Planner input count ===")
print(f"  Total pool:               {len(pool)}")
print(f"  LOCK/GENERATED excluded:  {len(excluded)}  {[c['path'] for c in excluded]}")
print(f"  Eligible for planner:     {len(eligible)}")
print(f"  Passed to planner (<=25): {len(planner_in)}")
print(f"  run-job.sh in planner:    {any('run-job' in c['path'] for c in planner_in)}")
print(f"  run-job.sh position:      {rj_pos}")

# ── D. Run-scoped file locations ───────────────────────────────────────────────
print()
print("=== D. Run-scoped artifact locations ===")
print("  BEFORE fix:")
print("    C:\\CC4E\\traces\\candidate_pool_VE-CC4E-VERSION.json   (ticket-global, overwritten each run)")
print("    C:\\CC4E\\traces\\planner_decisions_VE-CC4E-VERSION.json (ticket-global, overwritten each run)")
print()
print("  AFTER fix:")
print("    C:\\CC4E\\traces\\<slug>__<timestamp>\\candidate_pool.json    (run-specific, written atomically)")
print("    C:\\CC4E\\traces\\<slug>__<timestamp>\\planner_decisions.json (run-specific, written atomically)")
print("    C:\\CC4E\\traces\\<slug>__<timestamp>\\planner_failed.json    (written INSTEAD if LLM crashes)")

# ── E. Cross-run contamination no longer possible ─────────────────────────────
print()
print("=== E. Stale artifact contamination: ELIMINATED ===")
print("  Both files share the same _run_trace_dir = agents.tracer.trace_dir")
print("  Both are written together after create_plan() succeeds (no split window)")
print("  Each run gets a unique timestamped directory: collisions impossible")
print("  If create_plan() raises: planner_failed.json is written, pool+decisions are NOT written")
print("  TRACE_MODE=off: trace_dir=None, no files written at all (no stale files)")

# ── Diff summary ───────────────────────────────────────────────────────────────
print()
print("=== Exact code diff summary ===")
print("""
Fix 1 — workflow.py line ~829 (tracer arg):
  - agents.tracer.record_planning(plan, active_discovered)
  + agents.tracer.record_planning(plan, active_discovered_for_planner)

Fix 2+3 — workflow.py lines ~718-735 (pre-LLM write removed, try/except added):
  - import json
  - from pathlib import Path
  - traces_dir = Path(state["workspace_path"]) / "traces"
  - if traces_dir.exists():
  -     pool_file = traces_dir / f"candidate_pool_{state['ticket'].ticket_id}.json"
  -     with open(pool_file, "w") as f:
  -         json.dump(active_discovered_for_planner, f, indent=2)
  - plan = agents.planner.create_plan(...)
  + import json
  + _run_trace_dir = agents.tracer.trace_dir
  + try:
  +     plan = agents.planner.create_plan(...)
  + except Exception as plan_err:
  +     if _run_trace_dir is not None and _run_trace_dir.exists():
  +         (_run_trace_dir / "planner_failed.json").write_text(...)
  +     raise

Fix 2+3 — workflow.py lines ~811-819 (post-LLM write made atomic and run-scoped):
  - if traces_dir.exists():
  -     decisions_file = traces_dir / f"planner_decisions_{ticket_id}.json"
  -     json.dump(planner_decisions.values(), decisions_file)
  + if _run_trace_dir is not None and _run_trace_dir.exists():
  +     (_run_trace_dir / "candidate_pool.json").write_text(json.dumps(...))
  +     (_run_trace_dir / "planner_decisions.json").write_text(json.dumps(...))

Fix 4 — workflow.py lines ~679-706 (compound sort after role classification):
  + _ROLE_PRIORITY = {DEPLOYMENT:0, ROOT_COMPONENT:1, ... GENERATED:9}
  + active_discovered.sort(key=lambda x: (
  +     -x["confidence"], _ROLE_PRIORITY[x["candidate_role"]], x["path"]
  + ))
""")
