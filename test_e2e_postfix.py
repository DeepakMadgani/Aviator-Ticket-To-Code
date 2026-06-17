"""
End-to-end ticket test - submit Ticket A and Ticket B to the live backend
and poll for completion, capturing the final ranking from trace files.
"""
import requests
import json
import time
from pathlib import Path

BASE     = "http://localhost:8002"
TRACE    = Path(r"C:\CC4E\traces")
REPO     = r"C:\CC4E"
PROJ     = "cc4e"

TICKET_A = {
    "project_id": PROJ,
    "ticket_id":  "TEST-UX-001",
    "repo_path":  REPO,
    "ticket_description": (
        "UX alignment isn't proper for deliverable add reviewer and reviewers pages."
    ),
}

TICKET_B = {
    "project_id": PROJ,
    "ticket_id":  "TEST-COUNT-001",
    "repo_path":  REPO,
    "ticket_description": (
        "Deliverable count beside 'Name' column displays incorrect value when filter is applied."
    ),
}


def submit(payload):
    r = requests.post(f"{BASE}/api/workflow/transparent/start", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["workflow_id"]


def poll_workflow(wf_id, max_sec=600):
    deadline = time.time() + max_sec
    last_step = ""
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/api/workflow/transparent/{wf_id}", timeout=15)
        except Exception:
            time.sleep(5)
            continue
        if r.status_code != 200:
            time.sleep(5)
            continue
        data = r.json()
        status = data.get("status", "?")
        step   = data.get("current_step", "")
        if step != last_step:
            print(f"  [{wf_id[:8]}] status={status}  step={step}")
            last_step = step
        if status in ("completed", "failed", "error"):
            return data
        time.sleep(5)
    return {"status": "timeout"}


def find_latest_trace(before):
    current = {d for d in TRACE.iterdir() if d.is_dir()} if TRACE.exists() else set()
    new = current - before
    return sorted(new, key=lambda d: d.stat().st_mtime, reverse=True)[0] if new else None


def print_trace_ranking(trace_dir):
    for candidate_file in ["02_discovery.json", "localization.json", "candidates.json"]:
        fp = trace_dir / candidate_file
        if fp.exists():
            candidates = json.loads(fp.read_text())
            if isinstance(candidates, list):
                print(f"  Ranking from {candidate_file}:")
                for i, c in enumerate(candidates[:12]):
                    path = c.get("path", "?")[-65:]
                    conf = c.get("confidence", c.get("unified_score", 0))
                    feat = c.get("features", {})
                    own  = feat.get("s_own", 0)
                    gr   = feat.get("s_gr", 0)
                    sym  = feat.get("s_sym", 0)
                    tag  = " <<< ANGULAR" if "deliverable-reviewer" in c.get("path", "") else ""
                    print(f"    #{i+1:2d} {conf:.3f}  own={own:.2f} gr={gr:.2f} sym={sym:.2f}  {path}{tag}")
                return
    print("  No ranking trace file found; check trace dir manually")


def run_ticket(name, payload):
    print(f"\n{'='*68}")
    print(f"SUBMITTING: {name}")
    print(f"  {payload['ticket_description'][:70]}")
    print("=" * 68)

    before = {d for d in TRACE.iterdir() if d.is_dir()} if TRACE.exists() else set()
    wf_id  = submit(payload)
    print(f"  Workflow ID: {wf_id}")

    result = poll_workflow(wf_id, max_sec=600)
    print(f"  Final status: {result.get('status')}")

    trace = find_latest_trace(before)
    if trace:
        print(f"  Trace dir: {trace.name}")
        print_trace_ranking(trace)
    else:
        print("  (no new trace directory detected)")
    return result


if __name__ == "__main__":
    print("Checking backend health...")
    health = requests.get(f"{BASE}/api/health", timeout=10).json()
    print(f"  {health}")

    r_a = run_ticket("Ticket A -- UX Alignment", TICKET_A)
    r_b = run_ticket("Ticket B -- Deliverable Count", TICKET_B)

    print("\n" + "=" * 68)
    print("SUMMARY")
    print("=" * 68)
    print(f"Ticket A status: {r_a.get('status')}")
    print(f"Ticket B status: {r_b.get('status')}")
