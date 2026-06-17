import json

with open(r"C:\CC4E\traces\ux_alignment_isn_t_proper_for_deliverabl__20260609_203958\03_discovery.json") as f:
    d = json.load(f)

candidates = d.get("candidates", [])
print(f"Total candidates: {len(candidates)}")
for i, c in enumerate(candidates, 1):
    sigs = ", ".join(c.get("signals", [])[:3])
    print(f"  {i:2d}.  {c['confidence']:.3f}  {c['path'][:90]}  [{sigs}]")
