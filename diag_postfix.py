"""
Post-fix diagnostic: verify Angular files rank correctly with full ticket text
"""
import sys
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend")

import os, json, importlib
os.environ["NEO4J_PASSWORD"] = "aviator-dev"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"

# Force reload of localization_agent to pick up edits
import ticket_to_code.agents.localization_agent as la_mod
importlib.reload(la_mod)
from ticket_to_code.agents.localization_agent import LocalizationAgent

agent = LocalizationAgent(r"C:\CC4E")

# Load actual ticket
with open(r"C:\CC4E\traces\ux_alignment_isn_t_proper_for_deliverabl__20260609_124049\01_ticket.json") as f:
    ticket_data = json.load(f)

ticket_text = f"{ticket_data['title']} {ticket_data['description']}"

print("=== TICKET A: UX Alignment ===")
print("Ticket summary (first paragraph):", ticket_text.split('\n\n')[0][:100])

# Show what concept_keywords are extracted
summary = ticket_text.split('\n\n')[0].strip()
ckw = agent._extract_keywords(summary)
print(f"Concept keywords from summary ({len(ckw)}): {ckw}")
print()

# Run discovery
print("Running discovery (top_n=20)...")
candidates = agent.discover_repository_candidates(ticket_text, top_n=20)

print(f"\n=== TOP 20 UNIFIED SCORES (POST-FIX) ===")
for i, c in enumerate(candidates[:20]):
    feat = c.get("features", {})
    tag = " <<< ANGULAR" if "deliverable-reviewer" in c["path"] else ""
    print(f"  #{i+1:2d} {c['confidence']:.3f}  "
          f"fs={feat.get('s_fs',0):.2f} sql={feat.get('s_sql',0):.2f} "
          f"sym={feat.get('s_sym',0):.2f} path={feat.get('s_path',0):.3f} "
          f"gr={feat.get('s_gr',0):.2f} own={feat.get('s_own',0):.2f}  "
          f"{c['path'][-60:]}{tag}")

print()
print("=== DELIVERABLE-REVIEWER FILES POSITION ===")
for i, c in enumerate(candidates[:20]):
    if "deliverable-reviewer" in c["path"]:
        feat = c.get("features", {})
        print(f"  #{i+1:2d} {c['confidence']:.3f}  {c['path']}")
        print(f"       fs={feat.get('s_fs',0):.3f} sql={feat.get('s_sql',0):.3f} "
              f"sym={feat.get('s_sym',0):.3f} path={feat.get('s_path',0):.3f} "
              f"gr={feat.get('s_gr',0):.3f} own={feat.get('s_own',0):.3f}")
