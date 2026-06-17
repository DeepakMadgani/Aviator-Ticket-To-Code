import requests, json, time
from pathlib import Path

BASE = "http://localhost:8002"
TRACE = Path(r"C:\CC4E\traces")

payload = {
    "project_id": "cc4e",
    "ticket_id": "TEST-COUNT-002",
    "repo_path": r"C:\CC4E",
    "ticket_description": "Deliverable count beside Name column displays incorrect value when filter is applied."
}

print("Submitting Ticket B...")
r = requests.post(f"{BASE}/api/workflow/transparent/start", json=payload, timeout=30)
wf_id = r.json()["workflow_id"]
print(f"Workflow ID: {wf_id}")

before = {d for d in TRACE.iterdir() if d.is_dir()}
deadline = time.time() + 600
last_step = ""
while time.time() < deadline:
    try:
        s = requests.get(f"{BASE}/api/workflow/transparent/{wf_id}", timeout=15).json()
        step = s.get("current_step", "")
        status = s.get("status", "?")
        if step != last_step:
            print(f"  [{wf_id[:8]}] {status}  step={step}")
            last_step = step
        if status in ("completed", "failed", "error"):
            print(f"Final: {status}")
            break
    except Exception as e:
        print(f"Poll error: {e}")
    time.sleep(5)

new_dirs = {d for d in TRACE.iterdir() if d.is_dir()} - before
trace = sorted(new_dirs, key=lambda d: d.stat().st_mtime, reverse=True)[0] if new_dirs else None
if trace:
    print(f"Trace: {trace.name}")
    for f in sorted(trace.iterdir(), key=lambda x: x.name):
        print(f"  {f.name}")

