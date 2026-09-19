"""Polyglot Companion Resolver.

Resolves companion contracts and relationships across languages and frameworks:
  - Angular: .component.html <-> .component.ts <-> .component.scss / .spec.ts
  - React / Next.js: .tsx / .jsx <-> .module.css / .css, component <-> custom hooks
  - Vue: .vue SFC or sibling script/style files
  - Blazor / .NET: .razor <-> .razor.cs
  - Python (Django/Flask/FastAPI): templates/**/*.html <-> views.py / routes.py
  - Java (Spring): templates/**/*.html <-> *Controller.java
  - Universal Unit Tests: *test*.py <-> *.py, *Test.java <-> *.java, *.spec.ts <-> *.ts

Resolves companions first from in-memory session changes (_session_files / memory),
then from workspace disk.
"""
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class PolyglotCompanionResolver:
    """Discovers companion files across frameworks, templates, styles, and tests."""

    COMPANION_PAIRS = [
        # Angular
        (".component.html", [".component.ts", ".ts", ".component.scss", ".component.css", ".component.spec.ts"]),
        (".component.ts", [".component.html", ".component.scss", ".component.css", ".component.spec.ts"]),
        (".component.scss", [".component.html", ".component.ts"]),
        # React / Next.js
        (".tsx", [".module.css", ".module.scss", ".css", ".test.tsx", ".spec.tsx"]),
        (".jsx", [".module.css", ".module.scss", ".css", ".test.jsx", ".spec.jsx"]),
        # Vue
        (".vue", [".ts", ".js", ".css", ".scss", ".spec.ts", ".test.ts"]),
        # Blazor / C#
        (".razor", [".razor.cs", ".razor.css"]),
        (".razor.cs", [".razor"]),
        # Python
        (".html", ["views.py", "routes.py", "app.py"]),
        (".j2", ["views.py", "routes.py", "app.py"]),
        ("test_", [".py"]),
        ("_test.py", [".py"]),
        # Java Spring
        (".java", ["Test.java", "Tests.java"]),
    ]

    @classmethod
    def resolve_companion(
        cls,
        file_path: str,
        session_files: Optional[Dict[str, str]] = None,
        workspace_path: Optional[str] = None,
    ) -> Optional[Tuple[str, str]]:
        """Find the primary companion file and its content.

        Returns:
            Tuple of (companion_relative_path, companion_content) or None.
        """
        if not file_path:
            return None

        norm_fp = file_path.replace("\\", "/")
        p = Path(norm_fp)
        session_files = session_files or {}
        session_norm = {k.replace("\\", "/").lower(): v for k, v in session_files.items()}
        ws = Path(workspace_path) if workspace_path else None

        # Check framework-specific rules
        candidates = cls._generate_candidate_paths(norm_fp)

        # 1. Check in-memory session files first (most authoritative for active changes)
        for cand in candidates:
            cand_norm = cand.lower()
            if cand_norm in session_norm:
                return cand, session_norm[cand_norm]
            for s_path, s_content in session_norm.items():
                if s_path.endswith("/" + cand_norm) or cand_norm.endswith("/" + s_path):
                    return s_path, s_content
            # Basename matching in session files
            cand_base = Path(cand).name.lower()
            for s_path, s_content in session_norm.items():
                if Path(s_path).name.lower() == cand_base:
                    return s_path, s_content

        # 2. Check disk (direct path or workspace relative)
        for cand in candidates:
            cand_p = Path(cand)
            if cand_p.is_file():
                try:
                    return str(cand_p).replace("\\", "/"), cand_p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
            if ws and ws.exists():
                ws_cand = ws / cand
                if ws_cand.is_file():
                    try:
                        rel = str(ws_cand.relative_to(ws)).replace("\\", "/")
                        return rel, ws_cand.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass
                # Also check sibling in same folder on disk
                sibling = (ws / p.parent) / cand_p.name
                if sibling.is_file():
                    try:
                        rel = str(sibling.relative_to(ws)).replace("\\", "/")
                        return rel, sibling.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass

        return None

    @classmethod
    def _generate_candidate_paths(cls, file_path: str) -> List[str]:
        """Generate plausible companion paths for a given source path."""
        p = Path(file_path)
        name = p.name
        parent = str(p.parent).replace("\\", "/")
        parent_prefix = f"{parent}/" if parent and parent != "." else ""
        candidates = []

        # Angular specific
        if name.endswith(".component.html"):
            base = name[:-len(".component.html")]
            candidates.append(f"{parent_prefix}{base}.component.ts")
            candidates.append(f"{parent_prefix}{base}.ts")
        elif name.endswith(".component.ts"):
            base = name[:-len(".component.ts")]
            candidates.append(f"{parent_prefix}{base}.component.html")
            candidates.append(f"{parent_prefix}{base}.component.scss")
            candidates.append(f"{parent_prefix}{base}.component.css")
        elif name.endswith(".component.scss") or name.endswith(".component.css"):
            base = name[:-len(".component.scss")] if name.endswith(".component.scss") else name[:-len(".component.css")]
            candidates.append(f"{parent_prefix}{base}.component.html")
            candidates.append(f"{parent_prefix}{base}.component.ts")
        elif name.endswith(".html"):
            base = name[:-len(".html")]
            candidates.append(f"{parent_prefix}{base}.component.ts")
            candidates.append(f"{parent_prefix}{base}.ts")
            # Python / Django / Flask view companions
            candidates.append(f"{parent_prefix}views.py")
            candidates.append(f"{parent_prefix}routes.py")
            if parent:
                parent_p = Path(parent)
                candidates.append(f"{parent_p.parent}/views.py".replace("\\", "/"))
                candidates.append(f"{parent_p.parent}/routes.py".replace("\\", "/"))

        # Blazor specific
        elif name.endswith(".razor"):
            base = name[:-len(".razor")]
            candidates.append(f"{parent_prefix}{base}.razor.cs")
        elif name.endswith(".razor.cs"):
            base = name[:-len(".razor.cs")]
            candidates.append(f"{parent_prefix}{base}.razor")

        # React / Vue
        elif name.endswith((".tsx", ".jsx", ".vue")):
            base = p.stem
            candidates.append(f"{parent_prefix}{base}.module.css")
            candidates.append(f"{parent_prefix}{base}.module.scss")
            candidates.append(f"{parent_prefix}{base}.css")
            candidates.append(f"{parent_prefix}use{base}.ts")

        # Tests <-> Source
        if name.startswith("test_") and name.endswith(".py"):
            base = name[len("test_"):]
            candidates.append(f"{parent_prefix}{base}")
        elif name.endswith((".spec.ts", ".test.ts")):
            base = name.replace(".spec.ts", ".ts").replace(".test.ts", ".ts")
            candidates.append(f"{parent_prefix}{base}")
        elif name.endswith("Test.java"):
            base = name[:-len("Test.java")] + ".java"
            candidates.append(f"{parent_prefix}{base}")

        return candidates
