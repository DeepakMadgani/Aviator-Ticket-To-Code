import sys, os
os.environ["NEO4J_PASSWORD"] = "aviator-dev"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform")

import warnings; warnings.filterwarnings("ignore")
from ticket_to_code.agents.localization_agent import LocalizationAgent

TICKET = """UX alignment isn't proper for deliverable add reviewer and reviewers pages.

Steps to reproduce:
Login to CC4E app
Navigate to project TPR1, contract C1, deliverable D02
Click Info icon, Select Reviewers, Click + beside Reviewers 0

Observed:
Add and Cancel buttons appear slightly superimposed
Blank space exists below footer area
Layout appears static on larger screens
CSS responsiveness issue suspected
"""

agent = LocalizationAgent(workspace_path=r"C:\CC4E")
candidates = agent.discover_repository_candidates(TICKET, top_n=20)

print("\n=== Full ranking ===")
for i, c in enumerate(candidates, 1):
    f = c.get("features", {})
    role = f.get("cluster_role", "?")[:6]
    own = (f.get("ownership_type") or "?")[:14]
    print(f"  #{i:>2}  {c['confidence']:.3f}  s_path={f.get('s_path',0):.2f}  s_gr={f.get('s_gr',0):.2f}  s_rel={f.get('s_rel',0):.2f}  s_own={f.get('s_own',0):.2f}  [{role}] [{own}]  {c['path']}")

print("\n=== Component file families ===")
for c in candidates:
    p = c["path"]
    if "deliverable-reviewer" in p.lower():
        f = c.get("features", {})
        print(f"\n  {p}")
        print(f"    score={c['confidence']:.3f}  s_fs={f.get('s_fs',0):.3f}  s_path={f.get('s_path',0):.3f}  s_gr={f.get('s_gr',0):.3f}  s_sym={f.get('s_sym',0):.3f}")
        print(f"    s_own={f.get('s_own',0):.3f}  s_rel={f.get('s_rel',0):.3f}  cluster_role={f.get('cluster_role','?')}  _primary_score={f.get('_primary_score',0):.3f}")
        print(f"    ownership_type={f.get('ownership_type','?')}")
        print(f"    signals={c.get('signals',[])}")
