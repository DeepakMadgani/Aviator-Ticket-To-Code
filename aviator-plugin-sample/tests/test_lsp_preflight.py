"""Focused tests for the LSP/compiler preflight decision (composition module).

Covers PART 8 scenarios 1-14 at the preflight level. Scenarios 15-17
(differently-named capability reuse, RAG-absence not forcing CREATE_NEW, Add
Members reuse) are covered by test_behavioral_understanding.py and
test_capability_reuse_enforcement.py and are NOT duplicated here.

Pure logic; no LLM, no workspace, no compiler — diagnostics are supplied.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ticket_to_code.agents.lsp_preflight import (
    run_preflight, PreflightOutcome, error_signature,
)

OWNED = {"src/app/foo.component.ts"}
PLANNED = {"src/app/foo.component.ts"}


def _p(diags, **kw):
    return run_preflight(diags, generated_files=OWNED, planned_files=PLANNED, **kw)


# 1. High-confidence missing import → targeted repair.
def test_missing_import_repairs():
    r = _p(["src/app/foo.component.ts(1,1): error TS2304: Cannot find name 'HttpClient'."])
    assert r.outcome == PreflightOutcome.REPAIR.value
    assert r.repairs and r.repairs[0].file_path.endswith("foo.component.ts")
    assert r.repairs[0].symbol == "HttpClient"


# 2. Unresolved symbol → targeted repair.
def test_unresolved_symbol_repairs():
    r = _p(["src/app/foo.component.ts(5,10): error TS2552: Cannot find name 'doThing'."])
    assert r.should_repair and r.repairs[0].symbol == "doThing"


# 3. Wrong method/property reference → targeted repair.
def test_wrong_property_repairs():
    r = _p(["src/app/foo.component.ts(8,2): error TS2339: Property 'members' does not exist on type 'MemberService'."])
    assert r.should_repair and r.repairs[0].symbol == "members"


# 4. Type mismatch → targeted repair (ticket-introduced).
def test_type_mismatch_repairs():
    r = _p(["src/app/foo.component.ts(9,1): error TS2322: Type 'string' is not assignable to type 'number'."])
    assert r.should_repair and r.repairs[0].file_path.endswith("foo.component.ts")


# 5. Cross-file contract mismatch in a generated file → targeted repair.
def test_cross_file_contract_mismatch_repairs():
    gen = {"src/app/a.service.ts"}
    r = run_preflight(
        ["src/app/a.service.ts(3,3): error TS2554: Expected 2 arguments, but got 1."],
        generated_files=gen, planned_files=gen,
    )
    assert r.should_repair and r.repairs[0].file_path.endswith("a.service.ts")


# 6. Pre-existing diagnostic (unowned file) → leave and continue, no repair.
def test_pre_existing_leaves_and_continues():
    r = _p(["src/app/legacy/other.ts(2,2): error TS2304: Cannot find name 'X'."])
    assert r.outcome == PreflightOutcome.LEAVE_AND_CONTINUE.value
    assert not r.repairs


# 7. Ambiguous diagnostic (no attributable file) → do not guess.
def test_ambiguous_does_not_guess():
    r = _p(["error: internal compiler failure without a file"])
    assert r.outcome == PreflightOutcome.LEAVE_AND_CONTINUE.value
    assert not r.repairs and "ambiguous" in r.reason.lower()


# 8. Timeout → leave and continue even with blocking diagnostics.
def test_timeout_leaves_and_continues():
    r = _p(["src/app/foo.component.ts(1,1): error TS2304: Cannot find name 'HttpClient'."],
           timed_out=True)
    assert r.outcome == PreflightOutcome.LEAVE_AND_CONTINUE.value and not r.repairs


# 9. No-response → never authorizes edits (incl. unrelated).
def test_no_response_never_edits():
    r = _p(["src/app/foo.component.ts(1,1): error TS2304: Cannot find name 'HttpClient'."],
           no_response=True)
    assert r.outcome == PreflightOutcome.LEAVE_AND_CONTINUE.value and not r.repairs


# 10. Repair followed by re-preflight (now clean).
def test_repair_then_clean_repreflight():
    d = "src/app/foo.component.ts(1,1): error TS2304: Cannot find name 'HttpClient'."
    r1 = _p([d])
    assert r1.should_repair
    r2 = _p([])  # after the fix, no diagnostics
    assert r2.outcome == PreflightOutcome.CLEAN.value and not r2.repairs


# 11 & bounded termination. Repeated diagnostic → STOP.
def test_repeated_diagnostic_stops():
    d = "src/app/foo.component.ts(1,1): error TS2304: Cannot find name 'HttpClient'."
    prev = {error_signature([d])}
    r = run_preflight([d], generated_files=OWNED, planned_files=PLANNED, prev_signatures=prev)
    assert r.outcome == PreflightOutcome.STOP.value and not r.repairs


# 12. Unrelated file among owned ones is not modified.
def test_unrelated_file_unchanged():
    r = _p([
        "src/app/foo.component.ts(1,1): error TS2304: Cannot find name 'HttpClient'.",
        "src/app/legacy/unrelated.ts(4,4): error TS2304: Cannot find name 'Z'.",
    ])
    assert r.should_repair
    files = {rep.file_path for rep in r.repairs}
    assert all("unrelated.ts" not in f for f in files)


# 13. Genuine backend-required change (backend file is a generated change target) → writable/repairable.
def test_backend_change_target_repairs():
    gen = {"svc/src/main/java/com/x/OrderService.java"}
    r = run_preflight(
        ["svc/src/main/java/com/x/OrderService.java:[10,5]: error: cannot find symbol method save()"],
        generated_files=gen, planned_files=gen,
    )
    assert r.should_repair and r.repairs[0].file_path.endswith("OrderService.java")


# 14. Relevant backend provider that is only a reference (not generated) → read-only, not repaired.
def test_backend_reference_not_repaired():
    r = run_preflight(
        ["svc/src/main/java/com/x/MemberService.java:[20,3]: error: cannot find symbol"],
        generated_files=OWNED, planned_files=PLANNED,  # backend provider NOT owned
    )
    assert r.outcome == PreflightOutcome.LEAVE_AND_CONTINUE.value
    assert all("MemberService.java" not in rep.file_path for rep in r.repairs)


# New architectural dependency → return to planning (not a blind repair).
def test_new_dependency_returns_to_planning():
    r = run_preflight(
        ["src/app/foo.component.ts(1,1): error TS2304: Cannot find name 'HttpClient'."],
        generated_files=OWNED, planned_files=PLANNED, new_dependency=True,
    )
    assert r.outcome == PreflightOutcome.RETURN_TO_PLANNING.value and not r.repairs


# Infrastructure-only diagnostics → leave and continue (no source repair).
def test_infrastructure_only_leaves():
    r = _p(["Could not resolve dependencies for project: Connection timed out"])
    assert r.outcome == PreflightOutcome.LEAVE_AND_CONTINUE.value and not r.repairs


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
