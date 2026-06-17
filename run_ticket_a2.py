import requests, json, time
from pathlib import Path

BASE = "http://localhost:8002"
TRACE = Path(r"C:\CC4E\traces")

payload = {
    "project_id": "cc4e",
    "ticket_id": "TEST-UX-003",
    "repo_path": r"C:\CC4E",
    "ticket_description": (
        "UX alignment isn't proper for deliverable add reviewer and reviewers pages.\n\n"
        "Steps to reproduce:\n\n"
        "Login to CC4E app https://bimgatewayservice-nitro.qe.bp-paas.otxlab.net/?subscription-name=blue, "
        "credential: abhimanyuk@opentext.com / Password@123\n"
        "Navigate to project TPR1, then contract C1, then deliverable D02\n"
        "Click on info icon of column named 'Info'\n"
        "Select 'Reviewers' label from dropdown, click on '+' icon beside label Reviewers 0\n"
        "Observe that on footer area buttons 'Add' & 'Cancel' are little superimposed in bottom area, "
        "also there's blank space below footer area when the page gets loaded in bigger dimension screen, "
        "page CSS seems static"
    )
}

print("Submitting Ticket A (with Steps to Reproduce)...")
r = requests.post(f"{BASE}/api/workflow/transparent/start", json=payload, timeout=30)
wf_id = r.json()["workflow_id"]
print(f"Workflow ID: {wf_id}")

before = {d for d in TRACE.iterdir() if d.is_dir()}
deadline = time.time() + 900

while time.time() < deadline:
    time.sleep(20)
    new_dirs = sorted({d for d in TRACE.iterdir() if d.is_dir()} - before,
                      key=lambda p: p.stat().st_mtime)
    if new_dirs:
        td = new_dirs[-1]
        files = sorted(td.iterdir(), key=lambda p: p.name)
        print(f"\nTrace: {td.name}")
        for f in files:
            print(f"  {f.name}")
        if any(f.name == "11_summary.json" for f in files):
            with open(td / "11_summary.json") as f:
                s = json.load(f)
            print(f"\nStatus : {s.get('status')}")
            print(f"Duration: {s.get('duration_seconds')}s")
            top = s.get("top_candidates", [])[:3]
            for c in top:
                print(f"  #{c['rank']}  {c['confidence']:.3f}  {c['path']}")
            if (td / "08_generation.json").exists():
                with open(td / "08_generation.json") as f:
                    g = json.load(f)
                written = g.get("generated_code_files", [])
                print(f"\nFiles written: {len(written)}")
                for w in written:
                    print(f"  {w['file_path']}  ({w['line_count']} lines)")
            break
else:
    print("Timeout")
