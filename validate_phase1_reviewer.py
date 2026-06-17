"""Phase 1 retrieval validation for Angular UX ticket.
Runs discover_repository_candidates with top_n=20, prints full signal breakdown.
No LLM, no Neo4j, no external dependencies.
"""
import sys, os
os.environ["NEO4J_PASSWORD"] = ""

sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform")

from ticket_to_code.agents.localization_agent import LocalizationAgent

TICKET = """UX alignment isn't proper for deliverable add reviewer and reviewers pages.

Steps to reproduce:

Login to CC4E app

Navigate to project TPR1

Navigate to contract C1

Navigate to deliverable D02

Click Info icon

Select Reviewers

Click + beside Reviewers 0

Observed:

Add and Cancel buttons appear slightly superimposed
Blank space exists below footer area
Layout appears static on larger screens
CSS responsiveness issue suspected
"""

agent = LocalizationAgent(workspace_path=r"C:\CC4E")

# Step 1: keywords
keywords = agent._extract_keywords(TICKET)
print("=" * 70)
print("STEP 1 -- KEYWORDS EXTRACTED FROM TICKET")
print("=" * 70)
print(keywords)
print(f"Total: {len(keywords)} keywords")
print()

# Step 2 + 3: full top-20 ranking
print("=" * 70)
print("STEP 2+3 -- TOP 20 CANDIDATES (full signal breakdown)")
print("=" * 70)
candidates = agent.discover_repository_candidates(TICKET, top_n=20)

header = f"{'#':>3}  {'score':>6}  {'s_path':>6}  {'s_sym':>5}  {'s_own':>5}  {'s_fs':>5}  {'s_sql':>5}  {'s_gr':>5}  path"
print(header)
print("-" * 140)

for i, c in enumerate(candidates, 1):
    f = c.get("features", {})
    gate = " [GATE]" if (f.get("s_path",0)==0 and f.get("s_sym",0)==0 and f.get("s_own",0)<0.05) else ""
    print(
        f"{i:>3}  {c['confidence']:>6.3f}  "
        f"{f.get('s_path',0):>6.3f}  "
        f"{f.get('s_sym',0):>5.1f}  "
        f"{f.get('s_own',0):>5.2f}  "
        f"{f.get('s_fs',0):>5.2f}  "
        f"{f.get('s_sql',0):>5.2f}  "
        f"{f.get('s_gr',0):>5.2f}  "
        f"{c['path']}{gate}"
    )

# Step 4: Angular component analysis
print()
print("=" * 70)
print("STEP 4 -- ANGULAR COMPONENT FAMILY ANALYSIS")
print("=" * 70)
ts_comp  = [(i+1,c) for i,c in enumerate(candidates) if c['path'].endswith('.component.ts')]
html_comp= [(i+1,c) for i,c in enumerate(candidates) if c['path'].endswith('.component.html')]
scss_comp= [(i+1,c) for i,c in enumerate(candidates) if c['path'].endswith('.component.scss')]
spec_comp= [(i+1,c) for i,c in enumerate(candidates) if c['path'].endswith('.component.spec.ts')]
services = [(i+1,c) for i,c in enumerate(candidates) if 'service' in c['path'].lower() and c['path'].endswith('.ts')]

def show_group(label, group):
    print(f"\n{label} ({len(group)} in top 20):")
    for rank, c in group:
        f = c.get("features", {})
        print(f"  Rank #{rank:>2}  score={c['confidence']:.3f}  s_path={f.get('s_path',0):.2f}  s_sym={f.get('s_sym',0):.1f}  s_own={f.get('s_own',0):.2f}  {c['path']}")

show_group("*.component.ts", ts_comp)
show_group("*.component.html", html_comp)
show_group("*.component.scss", scss_comp)
show_group("*.component.spec.ts", spec_comp)
show_group("services", services)

# Step 5: top selected file detail
print()
print("=" * 70)
print("STEP 5 -- LOCALIZATION ANALYSIS")
print("=" * 70)
if candidates:
    top = candidates[0]
    f = top.get("features", {})
    print(f"Selected file (Rank #1):  {top['path']}")
    print(f"  score={top['confidence']:.3f}  s_path={f.get('s_path',0):.3f}  s_sym={f.get('s_sym',0):.1f}  s_own={f.get('s_own',0):.3f}")
    print(f"  signals: {top.get('signals', [])}")
    print()
    if len(candidates) > 1:
        print("Alternatives:")
        for i, c in enumerate(candidates[1:5], 2):
            f2 = c.get("features", {})
            print(f"  Rank #{i}  {c['confidence']:.3f}  {c['path']}")

# Step 7: reviewer-related specific search
print()
print("=" * 70)
print("STEP 7 -- REVIEWER / REVIEWERS FILE SEARCH")
print("=" * 70)
reviewer_candidates = [(i+1, c) for i, c in enumerate(candidates)
                       if any(x in c['path'].lower() for x in ['reviewer', 'review'])]
if reviewer_candidates:
    for rank, c in reviewer_candidates:
        f = c.get("features", {})
        print(f"  Rank #{rank:>2}  score={c['confidence']:.3f}  s_path={f.get('s_path',0):.2f}  s_sym={f.get('s_sym',0):.1f}  s_own={f.get('s_own',0):.2f}")
        print(f"           {c['path']}")
else:
    print("  NO reviewer-related files in top 20")

# Backend Java dominance check
print()
print("=" * 70)
print("STEP 6 -- BACKEND DOMINANCE CHECK")
print("=" * 70)
java_top5 = [(i+1,c) for i,c in enumerate(candidates[:5]) if c['path'].endswith('.java')]
ts_top5   = [(i+1,c) for i,c in enumerate(candidates[:5]) if c['path'].endswith('.ts') or c['path'].endswith('.html') or c['path'].endswith('.scss')]
print(f"Java files in top 5: {len(java_top5)}")
for rank, c in java_top5:
    print(f"  Rank #{rank}  {c['confidence']:.3f}  {c['path']}")
print(f"Frontend files (.ts/.html/.scss) in top 5: {len(ts_top5)}")
for rank, c in ts_top5:
    print(f"  Rank #{rank}  {c['confidence']:.3f}  {c['path']}")

print()
print("=" * 70)
print("STEP 8 -- GO / NO-GO")
print("=" * 70)
reviewer_in_top5 = any('reviewer' in c['path'].lower() for _, c in enumerate(candidates[:5]))
java_dominates  = len(java_top5) >= 3
if reviewer_in_top5 and not java_dominates:
    print("GO")
elif not reviewer_in_top5 and not java_dominates:
    print("MARGINAL -- frontend dominates but reviewer files not in top 5")
elif java_dominates:
    print("NO-GO -- Java files dominate top 5")
else:
    print("NO-GO -- reviewer ownership not found")
