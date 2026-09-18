"""Angular Component Controller to Template Contract.

Extracts template-facing public properties and methods from generated TypeScript
component controllers, injects explicit contracts into template generation prompts,
and deterministically validates that HTML bindings do not hallucinate phantom fields.
Follows the pattern: Validate -> Targeted Model Repair -> Revalidate.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple


# Regex to match class definition in TypeScript
_CLASS_RE = re.compile(
    r"""(?:export\s+)?class\s+([A-Za-z0-9_$]+)""",
    re.MULTILINE,
)

# Regex to match class field/property declarations:
# e.g.: isExistingMember: boolean = false;
#       existingOrganizationName: string = '';
#       userExistError: boolean;
_FIELD_RE = re.compile(
    r"""^\s*(?:public\s+)?(?:readonly\s+)?([A-Za-z0-9_$]+)\s*(?::\s*([^=;,\n]+))?(?:\s*=\s*([^;,\n]+))?;""",
    re.MULTILINE,
)

# Regex to match component methods:
# e.g.: onUserSelect(searchData) { ... }
_METHOD_RE = re.compile(
    r"""^\s*(?:public\s+)?([A-Za-z0-9_$]+)\s*\(([^)]*)\)\s*(?::\s*([^{]+))?\s*\{""",
    re.MULTILINE,
)

# Regex for HTML template bindings:
_NG_IF_RE = re.compile(r"""\*ngIf\s*=\s*["']([^"']+)["']""")
_INTERPOLATION_RE = re.compile(r"""\{\{\s*([^}]+)\s*\}\}""")
_BINDING_RE = re.compile(r"""\[(?:[a-zA-Z0-9_.-]+)\]\s*=\s*["']([^"']+)["']""")
_EVENT_RE = re.compile(r"""\((?:[a-zA-Z0-9_.-]+)\)\s*=\s*["']([^"']+)["']""")


@dataclass
class ComponentContract:
    """Public template-facing contract of an Angular component controller."""
    component_file: str
    class_name: str
    properties: Dict[str, str] = field(default_factory=dict)  # name -> type
    methods: Dict[str, str] = field(default_factory=dict)      # name -> signature

    @classmethod
    def extract_from_ts(cls, ts_content: str, file_path: str = "") -> "ComponentContract":
        """Extract public template-facing properties and methods from a component's TS code."""
        cm = _CLASS_RE.search(ts_content)
        class_name = cm.group(1) if cm else Path(file_path).stem

        properties: Dict[str, str] = {}
        # Parse fields
        for fm in _FIELD_RE.finditer(ts_content):
            fname = fm.group(1)
            # Skip lifecycle or private conventions
            if fname.startswith("_") or fname in ("constructor", "ngOnInit", "ngOnDestroy", "ngAfterViewInit"):
                continue
            ftype = (fm.group(2) or "any").strip()
            properties[fname] = ftype

        methods: Dict[str, str] = {}
        # Parse methods
        for mm in _METHOD_RE.finditer(ts_content):
            mname = mm.group(1)
            if mname.startswith("_") or mname in ("constructor", "ngOnInit", "ngOnDestroy", "ngAfterViewInit"):
                continue
            args = mm.group(2).strip()
            ret = (mm.group(3) or "void").strip()
            methods[mname] = f"{mname}({args}): {ret}"

        return cls(
            component_file=str(file_path).replace("\\", "/"),
            class_name=class_name,
            properties=properties,
            methods=methods,
        )

    def render_prompt_block(self) -> str:
        """Render the CONTROLLER CONTRACT block to instruct template generation."""
        lines = [
            "════════════════════════════════════════════════════════════════",
            f"CONTROLLER CONTRACT FOR TEMPLATE: {self.class_name}",
            f"Source: {self.component_file}",
            "════════════════════════════════════════════════════════════════",
            "The component controller exposes ONLY the following fields for template binding:",
        ]

        lines.append("\nAVAILABLE CONTROLLER PROPERTIES:")
        for pname, ptype in sorted(self.properties.items()):
            lines.append(f"  • {pname} ({ptype})")

        lines.append("\nAVAILABLE CONTROLLER METHODS:")
        for mname, msig in sorted(self.methods.items()):
            lines.append(f"  • {msig}")

        lines.append("\nTEMPLATE BINDING INVARIANTS:")
        lines.append("  1. Bind ONLY to the controller properties and methods listed above.")
        lines.append("  2. Do NOT invent phantom properties on objects (e.g. do NOT use")
        lines.append("     selectedUser.isExistingProjectMember if isExistingMember is on the controller).")
        lines.append("  3. Use *ngIf=\"isExistingMember\" or the exact controller property names.")
        lines.append("════════════════════════════════════════════════════════════════\n")

        return "\n".join(lines)


class TemplateContractValidator:
    """Validates an Angular HTML template against a ComponentContract."""

    @staticmethod
    def extract_bound_identifiers(html_content: str) -> Set[str]:
        """Extract all identifier tokens referenced in template expressions."""
        identifiers: Set[str] = set()

        # Collect all expression strings
        expressions = []
        for m in _NG_IF_RE.finditer(html_content):
            expressions.append(m.group(1))
        for m in _INTERPOLATION_RE.finditer(html_content):
            expressions.append(m.group(1))
        for m in _BINDING_RE.finditer(html_content):
            expressions.append(m.group(1))

        # Extract tokens like `foo.bar` or `isExistingMember`
        for expr in expressions:
            # Match member accesses: e.g. obj.property
            for mm in re.finditer(r"""([A-Za-z_$][A-Za-z0-9_$]*)(?:\.([A-Za-z_$][A-Za-z0-9_$]*))?""", expr):
                top_id = mm.group(1)
                sub_id = mm.group(2)
                # Skip JS / Angular keywords
                if top_id in ("true", "false", "null", "undefined", "let", "as", "index", "first", "last"):
                    continue
                identifiers.add(top_id)
                if sub_id:
                    identifiers.add(f"{top_id}.{sub_id}")

        return identifiers

    @classmethod
    def validate(cls, html_content: str, contract: ComponentContract) -> List[str]:
        """Validate template expressions against the controller contract.

        Returns:
            List of violation error messages (empty if completely valid).
        """
        violations: List[str] = []
        bound_ids = cls.extract_bound_identifiers(html_content)

        known_props = set(contract.properties.keys())
        known_methods = set(contract.methods.keys())

        # Check for commonly hallucinated compound properties:
        # e.g. "selectedUser.isExistingProjectMember" when controller has "isExistingMember"
        for bid in bound_ids:
            if "." in bid:
                root_obj, field = bid.split(".", 1)
                # If field looks like a state boolean but is not on controller, flag it
                if any(k in field.lower() for k in ("isexisting", "exist", "member", "project")):
                    if field not in known_props:
                        # Check if controller has a similar property via fuzzy match or token overlap
                        import difflib
                        candidates = difflib.get_close_matches(field, list(known_props), n=1, cutoff=0.45)
                        if not candidates:
                            field_lower = field.lower()
                            candidates = [p for p in known_props if p.lower() in field_lower or any(part in p.lower() for part in ("existing", "member", "exist") if part in field_lower)]
                        cand_hint = f" (did you mean '{candidates[0]}' on the controller?)" if candidates else ""
                        violations.append(
                            f"Binding '{bid}' references property '{field}' which is not exposed on controller{cand_hint}"
                        )

        return violations
