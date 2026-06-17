"""
Phase 3B validation: simulate EvidenceRankingEngine for Ticket A and B.

Ticket A: "Update CC4E version from 26.2 to 26.3"
  Expected top ranks: run-job.sh, app.component.ts

Ticket B: "UX alignment isn't proper for deliverable add reviewer and reviewers pages"
  Expected top ranks: deliverable-reviewers.component.scss, edit-deliverable.component.scss
"""
import sys
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")

from ticket_to_code.agents.evidence_ranking_engine import EvidenceRankingEngine
from ticket_to_code.models import EvidenceItem, InvestigationHypothesis, DevelopmentTask

def make_hyp(id, literals, symbols):
    return InvestigationHypothesis(
        id=id,
        hypothesis="test",
        literals=literals,
        symbols=symbols,
        confidence=0.8,
    )

def run_ticket_a():
    """Ticket A: version update"""
    hypotheses = [
        make_hyp("H1", ["26.2","26.3","260200","260300"], ["GHS_VERSION","ghsHelpVersion","helpVersion","APP_VERSION"]),
        make_hyp("H2", ["26.2","260200","run-job","helm"], ["run-job","helm","values.yaml","deployment"]),
    ]

    # Build synthetic evidence from repository_search.json
    import json
    from pathlib import Path
    rs = json.loads(Path(r"C:\CC4E\.aviator\repository_search.json").read_text(encoding="utf-8"))

    # Deduplicate to simulate single-flush (post-fix)
    seen = set()
    items = []
    for r in rs:
        key = (r["file_path"], r["query"], r["line_number"])
        if key in seen:
            continue
        seen.add(key)
        # content_snippet mirrors _query_repository_search output
        if r["query_type"] == "literal":
            snippet = f"[literal:{r['query']!r}] line {r['line_number']}: {r['matched_text']}"
        else:
            snippet = f"[filename:{r['query']!r}] {r['matched_text']}"
        items.append(EvidenceItem(
            source="repository_search",
            file_path=r["file_path"],
            evidence_type="usage" if r["query_type"] == "literal" else "definition",
            content_snippet=snippet[:400],
            relevance_score=r["confidence"],
            hypothesis_id="H1",
        ))

    ranker = EvidenceRankingEngine()
    _, ranked = ranker.rank(items, hypotheses, [])

    print("\n====== TICKET A: Version Update ======")
    print("Top 15 ranked files:")
    for rf in ranked[:15]:
        lits = f"  lits={rf.matched_literals}" if rf.matched_literals else ""
        pen  = f"  PENALTIES={rf.penalties}" if rf.penalties else ""
        print(f"  #{rf.rank:2d}  {rf.final_score:.3f}  {rf.file_path}{lits}{pen}")

    targets = ["run-job.sh", "app.component.ts"]
    print()
    for t in targets:
        match = next((r for r in ranked if t in r.file_path), None)
        if match and match.rank <= 5:
            print(f"  ✅ PASS  {match.file_path}  rank=#{match.rank}  score={match.final_score:.3f}")
        elif match:
            print(f"  ⚠️  PRESENT but rank=#{match.rank}  score={match.final_score:.3f}  {match.file_path}")
        else:
            print(f"  ❌ ABSENT  {t}")

def run_ticket_b():
    """Ticket B: UX alignment - simulate evidence from CSS/ownership chains"""
    hypotheses = [
        make_hyp("H1",
                 ["deliverable-reviewers", "deliverable-add-reviewer", "add-reviewer"],
                 ["DeliverableReviewersComponent", "EditDeliverableComponent",
                  "deliverable-reviewers", "edit-deliverable"]),
    ]

    # Simulate evidence items typical for a UX/CSS ticket
    raw_items = [
        # CSS chain — the target files
        dict(source="css_chain", fp="xchange-ui/src/app/modules/deliverables/deliverable-reviewers/deliverable-reviewers.component.scss", score=0.82, snip="css chain: .deliverable-reviewers { padding: 0; }"),
        dict(source="css_chain", fp="xchange-ui/src/app/modules/deliverables/deliverable-edit/edit-deliverable.component.scss", score=0.78, snip="css chain: .edit-deliverable { margin: 0; }"),
        dict(source="sqlite_symbol", fp="xchange-ui/src/app/modules/deliverables/deliverable-reviewers/deliverable-reviewers.component.ts", score=0.80, snip="class: DeliverableReviewersComponent", sym="DeliverableReviewersComponent"),
        dict(source="sqlite_symbol", fp="xchange-ui/src/app/modules/deliverables/deliverable-edit/edit-deliverable.component.ts", score=0.80, snip="class: EditDeliverableComponent", sym="EditDeliverableComponent"),
        # ts chain companions
        dict(source="ts_chain", fp="xchange-ui/src/app/modules/deliverables/deliverable-reviewers/deliverable-reviewers.component.html", score=0.72, snip="template: <app-deliverable-reviewers>"),
        # Noise: unrelated scss
        dict(source="css_chain", fp="xchange-ui/src/app/modules/deliverables/content-list/content-list.component.scss", score=0.65, snip="css chain: .content-list { }"),
        # Noise: repo search filename-only
        dict(source="repository_search", fp="xchange-ui/src/app/modules/deliverables/deliverable-reviewers/deliverable-add-reviewer/deliverable-add-reviewer.component.scss", score=0.88, snip="[filename:'deliverable-reviewers'] xchange-ui/.../deliverable-add-reviewer.component.scss"),
        dict(source="repository_search", fp="area-service/helm/templates/hpa.yaml", score=0.88, snip="[filename:'helm'] area-service/helm/templates/hpa.yaml"),
        dict(source="repository_search", fp="area-service/helm/Chart.yaml", score=0.88, snip="[filename:'helm'] area-service/helm/Chart.yaml"),
        # semantic — partial signal
        dict(source="semantic", fp="xchange-ui/src/app/modules/deliverables/deliverable-reviewers/deliverable-reviewers.component.scss", score=0.68, snip="reviewer alignment styles"),
    ]
    items = []
    for r in raw_items:
        e = EvidenceItem(
            source=r["source"],
            file_path=r["fp"],
            evidence_type="definition",
            content_snippet=r["snip"],
            relevance_score=r["score"],
            hypothesis_id="H1",
        )
        if "sym" in r:
            e.symbol_name = r["sym"]
        items.append(e)

    # Localized tasks (plan)
    from ticket_to_code.models import DevelopmentTask, TaskType, ProgrammingLanguage
    localized = [
        DevelopmentTask(
            id="t1", title="deliverable-reviewers.component.scss", description="",
            file_path="xchange-ui/src/app/modules/deliverables/deliverable-reviewers/deliverable-reviewers.component.scss",
            task_type=TaskType.MODIFY, language=ProgrammingLanguage.TYPESCRIPT,
        ),
    ]

    ranker = EvidenceRankingEngine()
    _, ranked = ranker.rank(items, hypotheses, localized)

    print("\n====== TICKET B: UX Alignment ======")
    print("Top 10 ranked files:")
    for rf in ranked[:10]:
        lits = f"  lits={rf.matched_literals}" if rf.matched_literals else ""
        pen  = f"  PENALTIES={rf.penalties}" if rf.penalties else ""
        syms = f"  syms={rf.matched_symbols}" if rf.matched_symbols else ""
        print(f"  #{rf.rank:2d}  {rf.final_score:.3f}  {rf.file_path.split('/')[-1]}{lits}{syms}{pen}")

    targets = ["deliverable-reviewers.component.scss", "edit-deliverable.component.scss"]
    print()
    for t in targets:
        match = next((r for r in ranked if t in r.file_path), None)
        if match and match.rank <= 5:
            print(f"  ✅ PASS  {match.file_path.split('/')[-1]}  rank=#{match.rank}  score={match.final_score:.3f}")
        elif match:
            print(f"  ⚠️  PRESENT but rank=#{match.rank}  score={match.final_score:.3f}  {match.file_path.split('/')[-1]}")
        else:
            print(f"  ❌ ABSENT  {t}")

if __name__ == "__main__":
    run_ticket_a()
    run_ticket_b()
