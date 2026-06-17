"""
Diagnostic: compute unified scores for Angular vs Java candidates
to understand why Angular files don't rank in top 20.
"""
import sys
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend")

import os
os.environ["NEO4J_PASSWORD"] = "aviator-dev"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"

from ticket_to_code.agents.localization_agent import LocalizationAgent

agent = LocalizationAgent(r"C:\CC4E")

ticket = "UX alignment isn't proper for deliverable add reviewer and reviewers pages"
keywords = agent._extract_keywords(ticket)
print("Keywords:", keywords)
print()

# Run the full discovery but trace at each step
# Phase 1: path walk
import os
from pathlib import Path

workspace = Path(r"C:\CC4E")
SOURCE_EXTS = {
    ".ts", ".tsx", ".js", ".jsx", ".html", ".scss", ".css", ".vue",
    ".java", ".cs", ".py", ".go", ".xml", ".json", ".yaml", ".yml"
}
SKIP_DIRS = {
    ".git", "node_modules", "target", ".aviator", "dist", "build",
    ".idea", "__pycache__", ".venv", ".angular", "coverage", "traces"
}

path_hits = []
for root, dirs, files in os.walk(workspace):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fname in files:
        full = Path(root) / fname
        if full.suffix.lower() not in SOURCE_EXTS:
            continue
        rel = str(full.relative_to(workspace)).replace("\\", "/")
        rel_lower = rel.lower()
        score = sum(1 for kw in keywords if kw.lower() in rel_lower)
        if score > 0:
            path_hits.append((score, full, rel))

path_hits.sort(key=lambda x: -x[0])
print(f"=== TOP 20 by PATH SCORE (total: {len(path_hits)}) ===")
for i, (sc, _, rel) in enumerate(path_hits[:20]):
    print(f"  #{i+1:2d} path_score={sc}  {rel[-80:]}")

print()
print("=== Does Angular deliverable-reviewers appear? ===")
angular_in_top = [(i, sc, rel) for i, (sc, _, rel) in enumerate(path_hits[:100]) 
                   if "deliverable-reviewer" in rel.lower()]
for idx, sc, rel in angular_in_top:
    print(f"  Position #{idx+1}, score={sc}, path={rel}")

print()
# Now show actual unified score for Angular component
angular_path = "xchange-ui/src/app/modules/deliverables/deliverable-reviewers/deliverable-reviewers.component.ts"
java_path = "area-service/src/test/java/com/opentext/solutions/services/area/domain/engineering/service/DeliverableAtomicTransactionServiceTest.java"

# Build a small raw_candidates with just these two
import re
from pathlib import Path as P

def make_raw(rel, ws=workspace):
    full = ws / rel
    try:
        content = full.read_text(encoding='utf-8', errors='ignore')
    except:
        content = ""
    kws = agent._extract_keywords(ticket)
    path_score = sum(1 for kw in kws if kw.lower() in rel.lower())
    top_kws = kws[:6]
    content_score = sum(1 for kw in top_kws if kw.lower() in content.lower())
    return {
        "path": rel,
        "confidence": 0.0,
        "signals": ["keyword_path"] + (["keyword_content"] if content_score > 0 else []),
        "raw_score": float(path_score + content_score),
        "_content": content,
    }

# Build a larger set to mimic actual discovery
raw = []
for sc, full, rel in path_hits[:100]:
    try:
        content = full.read_text(encoding='utf-8', errors='ignore')
    except:
        content = ""
    top_kws = keywords[:6]
    content_score = sum(1 for kw in top_kws if kw.lower() in content.lower())
    raw.append({
        "path": rel,
        "confidence": 0.0,
        "signals": ["keyword_path"] + (["keyword_content"] if content_score > 0 else []),
        "raw_score": float(sc + content_score),
        "_content": content,
    })
raw.sort(key=lambda x: -x["raw_score"])

print("=== TOP 20 raw candidates (path+content score) BEFORE unified ranking ===")
for i, c in enumerate(raw[:20]):
    print(f"  #{i+1:2d} raw={c['raw_score']:.0f}  {c['path'][-75:]}")

print()
# Find Angular position in raw candidates
angular_pos = next((i for i, c in enumerate(raw) if "deliverable-reviewer" in c["path"] and c["path"].endswith(".ts")), None)
print(f"Angular component position in raw_candidates: #{(angular_pos+1) if angular_pos is not None else 'NOT FOUND'}")

print()
print("=== Running analyze_candidates ===")
enriched = agent.analyze_candidates(raw[:50], ticket)

print("\n=== TOP 20 UNIFIED SCORES ===")
for i, c in enumerate(enriched[:20]):
    feat = c.get("features", {})
    print(f"  #{i+1:2d} unified={c['unified_score']:.3f}  "
          f"s_fs={feat.get('s_fs',0):.2f} s_sql={feat.get('s_sql',0):.2f} "
          f"s_sym={feat.get('s_sym',0):.2f} s_path={feat.get('s_path',0):.3f} "
          f"s_gr={feat.get('s_gr',0):.2f} s_own={feat.get('s_own',0):.2f}  "
          f"{c['path'][-60:]}")

print()
print("=== Angular deliverable-reviewers position ===")
for i, c in enumerate(enriched):
    if "deliverable-reviewer" in c["path"]:
        feat = c.get("features", {})
        print(f"  #{i+1:2d} unified={c['unified_score']:.3f}  "
              f"s_fs={feat.get('s_fs',0):.3f} s_sql={feat.get('s_sql',0):.3f} "
              f"s_sym={feat.get('s_sym',0):.3f} s_path={feat.get('s_path',0):.3f} "
              f"s_gr={feat.get('s_gr',0):.3f} s_own={feat.get('s_own',0):.3f}  "
              f"{c['path']}")
