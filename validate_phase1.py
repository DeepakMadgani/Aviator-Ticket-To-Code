"""Phase 1 validation: ranking test against real version ticket.
Runs discover_repository_candidates with top_n=20, prints full signal breakdown.
No LLM, no Neo4j, no external dependencies.
"""
import sys, os
os.environ["NEO4J_PASSWORD"] = ""

sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform")

from ticket_to_code.agents.localization_agent import LocalizationAgent

# ------- ticket under test -------
TICKET_SHORT = "Update CC4E Gen3 dashboard version from 26.2 to 26.3"

TICKET_FULL = """Update CC4E Gen3 dashboard version from 26.2 to 26.3

CC4E Gen3 version on classic & JATO dashboard needs to be updated to 26.3

Steps to observe:
Login to https://bimgatewayservice-nitro.qe.bp-paas.otxlab.net/subscriptions/blue/home
Observe that version on header beside label Core Collaboration for Engineering is currently displayed as 26.2
Update the same to 26.3
Then click on button labeled AK, click on Switch to Jato View
Observe the same behavior as mentioned in above bullet point
Execute the same step as mentioned in bullet point
"""

agent = LocalizationAgent(workspace_path=r"C:\CC4E")

# Validate keyword extraction first
keywords = agent._extract_keywords(TICKET_FULL)
print("=" * 70)
print("KEYWORDS EXTRACTED FROM TICKET")
print("=" * 70)
print(keywords)
print()

print("=" * 70)
print("TOP 20 CANDIDATES — FULL SIGNAL BREAKDOWN")
print("Ticket:", TICKET_SHORT)
print("=" * 70)

candidates = agent.discover_repository_candidates(TICKET_FULL, top_n=20)

TARGET = "jheader.component.ts"
NEGATIVE = "GenericControllerHandler"

target_rank = None
negatives_found = []

header = f"{'#':>3}  {'conf':>6}  {'s_path':>6}  {'s_sym':>5}  {'s_own':>5}  {'s_fs':>5}  {'s_sql':>5}  {'s_gr':>5}  {'signals':<30}  path"
print(header)
print("-" * 130)

for i, c in enumerate(candidates, 1):
    feats = c.get("features", {})
    sigs  = ",".join(c.get("signals", [])[:4])
    gate_note = ""
    if (feats.get("s_path", 0) == 0 and feats.get("s_sym", 0) == 0
            and feats.get("s_own", 0) < 0.05):
        gate_note = " [GATED×0.15]"
    path = c["path"]
    print(
        f"{i:>3}  {c['confidence']:>6.3f}  "
        f"{feats.get('s_path',0):>6.3f}  "
        f"{feats.get('s_sym',0):>5.1f}  "
        f"{feats.get('s_own',0):>5.2f}  "
        f"{feats.get('s_fs',0):>5.2f}  "
        f"{feats.get('s_sql',0):>5.2f}  "
        f"{feats.get('s_gr',0):>5.2f}  "
        f"{sigs:<30}  {path}{gate_note}"
    )
    if TARGET in path and target_rank is None:
        target_rank = i
    if NEGATIVE in path:
        negatives_found.append((i, path, c['confidence']))

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
if target_rank:
    t = candidates[target_rank - 1]
    f = t.get("features", {})
    print(f"TARGET  jheader.component.ts  → Rank #{target_rank}")
    print(f"  conf={t['confidence']:.3f}  s_path={f.get('s_path',0):.3f}  "
          f"s_sym={f.get('s_sym',0):.1f}  s_own={f.get('s_own',0):.3f}  "
          f"s_fs={f.get('s_fs',0):.3f}  s_sql={f.get('s_sql',0):.3f}")
else:
    print("TARGET  jheader.component.ts  → NOT IN TOP 20")

if negatives_found:
    print()
    print("NEGATIVES still appearing in top 20:")
    for rank, path, conf in negatives_found:
        print(f"  Rank #{rank}  conf={conf:.3f}  {path}")
else:
    print()
    print("NEGATIVES (GenericControllerHandler*) → NOT in top 20  ✓")

print()
print("=" * 70)
print("GO / NO-GO SIGNAL EVIDENCE")
print("=" * 70)
if target_rank and target_rank <= 3 and not negatives_found:
    print("GO:  jheader in top-3, no false-positive exception handlers in top 20.")
elif target_rank and target_rank <= 5 and not negatives_found:
    print(f"GO (marginal): jheader at rank #{target_rank}, no negatives.")
elif target_rank and target_rank <= 5:
    print(f"MARGINAL: jheader at rank #{target_rank} but negatives present.")
else:
    print(f"NO-GO: jheader outside top-5 (rank #{target_rank}) or negatives present.")
