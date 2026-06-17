"""
Diagnostic with FULL ticket text (title + description)
"""
import sys
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend")

import os, json
os.environ["NEO4J_PASSWORD"] = "aviator-dev"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"

from ticket_to_code.agents.localization_agent import LocalizationAgent

agent = LocalizationAgent(r"C:\CC4E")

# Load the actual ticket from trace
with open(r"C:\CC4E\traces\ux_alignment_isn_t_proper_for_deliverabl__20260609_124049\01_ticket.json") as f:
    ticket_data = json.load(f)

ticket_title = ticket_data["title"]
ticket_desc = ticket_data["description"]
ticket_text = f"{ticket_title} {ticket_desc}"

print("=== Ticket text (first 200 chars) ===")
print(ticket_text[:200])
print()

keywords = agent._extract_keywords(ticket_text)
print(f"=== Keywords ({len(keywords)} total) ===")
print(keywords[:20], "...")
print()

# Full discovery run
print("=== Running full discovery ===")
candidates = agent.discover_repository_candidates(ticket_text, top_n=30)

print("\n=== TOP 20 UNIFIED SCORES (full discovery) ===")
for i, c in enumerate(candidates[:20]):
    feat = c.get("features", {})
    print(f"  #{i+1:2d} unified={c['confidence']:.3f}  "
          f"fs={feat.get('s_fs',0):.2f} sql={feat.get('s_sql',0):.2f} "
          f"sym={feat.get('s_sym',0):.2f} path={feat.get('s_path',0):.3f} "
          f"gr={feat.get('s_gr',0):.2f} own={feat.get('s_own',0):.2f}  "
          f"{c['path'][-65:]}")

print()
print("=== Angular deliverable-reviewers position ===")
found = False
for i, c in enumerate(candidates):
    if "deliverable-reviewer" in c["path"]:
        feat = c.get("features", {})
        print(f"  #{i+1:2d} unified={c['confidence']:.3f}  "
              f"fs={feat.get('s_fs',0):.3f} sql={feat.get('s_sql',0):.3f} "
              f"sym={feat.get('s_sym',0):.3f} path={feat.get('s_path',0):.3f} "
              f"gr={feat.get('s_gr',0):.3f} own={feat.get('s_own',0):.3f}  "
              f"{c['path']}")
        found = True
if not found:
    print("  NOT IN TOP 30 — below discovery cutoff!")
