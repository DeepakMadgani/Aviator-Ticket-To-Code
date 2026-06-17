"""
Post-fix diagnostic for Ticket B: Deliverable count filter bug
"""
import sys
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend")

import os, importlib
os.environ["NEO4J_PASSWORD"] = "aviator-dev"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"

import ticket_to_code.agents.localization_agent as la_mod
importlib.reload(la_mod)
from ticket_to_code.agents.localization_agent import LocalizationAgent

agent = LocalizationAgent(r"C:\CC4E")

TICKET_B = """Deliverable count beside 'Name' column displays incorrect value when filter is applied.

Steps to Reproduce:
1. Login with credentials: https://ot2-dev.opentext.com/ abhimanyuk@opentext.com
2. Navigate to Deliverables module
3. Apply a filter on the deliverable list
4. Observe the count shown beside the 'Name' column header

Expected: Count should reflect filtered list
Actual: Count shows total count, ignoring the applied filter
"""

print("=== TICKET B: Deliverable Count Filter ===")
summary = TICKET_B.split('\n\n')[0].strip()
ckw = agent._extract_keywords(summary)
print(f"Concept keywords from summary ({len(ckw)}): {ckw}")
print()

candidates = agent.discover_repository_candidates(TICKET_B, top_n=20)

print(f"=== TOP 20 (POST-FIX) ===")
for i, c in enumerate(candidates[:20]):
    feat = c.get("features", {})
    tag = " <<< Angular" if c["path"].endswith(".ts") and "deliverable" in c["path"] else ""
    print(f"  #{i+1:2d} {c['confidence']:.3f}  "
          f"fs={feat.get('s_fs',0):.2f} sql={feat.get('s_sql',0):.2f} "
          f"sym={feat.get('s_sym',0):.2f} path={feat.get('s_path',0):.3f} "
          f"own={feat.get('s_own',0):.2f}  "
          f"{c['path'][-65:]}{tag}")
