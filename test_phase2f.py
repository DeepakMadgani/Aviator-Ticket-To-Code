import sys, os
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"

from pathlib import Path
from ticket_to_code.workflow import (
    _find_layout_gap_owners, _extract_top_level_classes,
    _class_has_layout_properties, _ticket_is_layout_focused
)

workspace = Path(r"C:\CC4E")

print("=== TICKET A: UX alignment (layout ticket) ===")
ticket_a = "ux alignment isn't proper for deliverable add reviewer and reviewers pages. Steps to reproduce: footer buttons superimposed blank space page css seems static"
print("is_layout:", _ticket_is_layout_focused(ticket_a.lower()))

scss_path = "xchange-ui/src/app/modules/deliverables/deliverable-reviewers/deliverable-reviewers.component.scss"
existing = {scss_path}
gaps = _find_layout_gap_owners(scss_path, workspace, existing)
print("Gap owners found:", len(gaps))
for g in gaps:
    is_host = g.get("is_host", False)
    marker = "HOST-COMPONENT" if is_host else "sibling"
    print(f"  [{marker}] {g['path']}  shared_classes={g['shared_classes']}")

print()
print("=== Verify edit-deliverable.component.scss layout classes ===")
parent_scss = workspace / "xchange-ui/src/app/modules/deliverables/edit-deliverable/edit-deliverable.component.scss"
content = parent_scss.read_text(encoding="utf-8", errors="ignore")
classes = _extract_top_level_classes(content)
print("Top-level classes:", sorted(classes))
for cls in sorted(classes):
    has_layout = _class_has_layout_properties(content, cls)
    print(f"  .{cls}: has_layout={has_layout}")

print()
print("=== TICKET B: Deliverable count (data ticket) ===")
ticket_b = "Deliverable count beside Name column displays incorrect value when filter is applied."
print("is_layout:", _ticket_is_layout_focused(ticket_b.lower()))

print()
print("=== TICKET C: Version update ===")
ticket_c = "Update CC4E Gen3 version from 26.2 to 26.3"
print("is_layout:", _ticket_is_layout_focused(ticket_c.lower()))

print()
print("=== Sibling SCSS files in deliverables/ ===")
module_dir = workspace / "xchange-ui/src/app/modules/deliverables"
for f in sorted(module_dir.rglob("*.scss")):
    rel = str(f.relative_to(workspace)).replace("\\", "/")
    c = f.read_text(encoding="utf-8", errors="ignore")
    top = _extract_top_level_classes(c)
    if "edit-deliv" in top:
        print(f"  {rel}  defines .edit-deliv")
