"""TypeScript source file parser using the TypeScript Compiler API via Node.js.

Extracts structural metadata from ``.ts`` files and emits the same
``ParseResult`` shape as ``JavaParser`` — allowing the indexer to treat
TypeScript as a first-class indexed language without schema changes.

Strategy
--------
1. **Primary path** — invoke Node.js with ``extract_ts_symbols.cjs`` (bundled
   alongside this module).  Results are cached by the file's SHA-256 so a
   second index run is instant.
2. **Fallback path** — if Node.js is unavailable or the ``typescript`` npm
   package cannot be located, a regex-based extractor covers the most common
   Angular patterns.  It produces the same row shape; precision is lower but
   recall is sufficient for retrieval/ranking purposes.

Relationship edges emitted
--------------------------
``CONTAINS``      file → class
``ANNOTATED_BY``  class → decorator symbol  (@Component, @Injectable, …)
``TEMPLATE_OF``   class → HTML template file path
``STYLE_OF``      class → SCSS/CSS style file path
``IMPORTS``       file → imported module path (relative imports resolved)
``HAS_TYPE``      constructor param → injected service class
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from aviator_core.models import (
    Edge,
    EdgeKind,
    FileRecord,
    SourceLocation,
    Symbol,
    SymbolKind,
)
from aviator_core.parsers.java_parser import ParseResult  # reuse same dataclass

logger = logging.getLogger(__name__)

_EXTRACTOR_SCRIPT = Path(__file__).with_name("extract_ts_symbols.cjs")

# Directories whose node_modules are searched for the typescript package.
_TS_SEARCH_SUBDIRS = ("", "xchange-ui", "ui", "frontend", "client", "web")

# ── Unique id helper (mirrors java_parser convention) ─────────────────────


def _make_id(path: str, kind: str, qname: str) -> str:
    key = f"{path}:{kind}:{qname}"
    return hashlib.sha1(key.encode()).hexdigest()[:20]


# ── Node.js detection ─────────────────────────────────────────────────────


def _find_node() -> Optional[str]:
    import shutil
    return shutil.which("node")


def _find_typescript_lib(repo_root: Path) -> Optional[str]:
    """Search common node_modules locations for the typescript package."""
    for sub in _TS_SEARCH_SUBDIRS:
        candidate = repo_root / sub / "node_modules" / "typescript" / "lib" / "typescript.js"
        if candidate.exists():
            return str(candidate)
    return None


# ── SHA-256 cache ─────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _cache_path(db_dir: Path, sha: str) -> Path:
    cache_dir = db_dir / "ts_cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir / f"{sha}.json"


# ── Node extraction ───────────────────────────────────────────────────────


def _extract_via_node(
    ts_file: Path,
    repo_root: Path,
    node_bin: str,
    ts_lib: Optional[str],
) -> Optional[dict]:
    """Run Node.js extractor; return parsed JSON or None on failure."""
    cmd = [node_bin, str(_EXTRACTOR_SCRIPT), str(ts_file), str(repo_root)]
    if ts_lib:
        cmd.append(ts_lib)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        if result.stderr:
            logger.debug(f"TS extractor stderr ({ts_file.name}): {result.stderr[:300]}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
        logger.debug(f"TS node extraction failed for {ts_file}: {exc}")
    return None


# ── Regex fallback ────────────────────────────────────────────────────────

# Decorator: @Component({...}), @Injectable(), @NgModule({...}), @Directive, @Pipe
_RE_DECORATOR = re.compile(
    r"@(?P<name>Component|Injectable|NgModule|Directive|Pipe|Guard|Resolver|Interceptor)"
    r"(?:\((?P<args>[^)]{0,800})\))?",
    re.DOTALL,
)
_RE_CLASS = re.compile(
    r"(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>\w+)"
    r"(?:\s+implements\s+(?P<implements>[\w,\s]+?))?(?:\s+extends\s+(?P<extends>\w+))?"
    r"\s*\{",
)
_RE_TEMPLATE_URL = re.compile(r"templateUrl\s*:\s*['\"](?P<url>[^'\"]+)['\"]")
_RE_STYLE_URLS = re.compile(r"styleUrls?\s*:\s*\[(?P<urls>[^\]]*)\]")
_RE_STYLE_URL_STR = re.compile(r"['\"](?P<url>[^'\"]+)['\"]")
_RE_SELECTOR = re.compile(r"selector\s*:\s*['\"](?P<selector>[^'\"]+)['\"]")
_RE_IMPORT = re.compile(
    r"^import\s+\{(?P<names>[^}]+)\}\s+from\s+['\"](?P<module>[^'\"]+)['\"]",
    re.MULTILINE,
)
_RE_CTOR = re.compile(
    r"constructor\s*\((?P<params>[^)]{0,600})\)",
    re.DOTALL,
)
_RE_CTOR_PARAM = re.compile(
    r"(?:private|protected|public|readonly|\s)+\s*(?P<pname>\w+)\s*:\s*(?P<type>\w+)"
)
_RE_EXPORT_CONST = re.compile(
    r"^export\s+(?:const|let|var)\s+(?P<name>\w+)\s*[=:]",
    re.MULTILINE,
)
_RE_EXPORT_FN = re.compile(
    r"^export\s+(?:async\s+)?function\s+(?P<name>\w+)\s*\(",
    re.MULTILINE,
)


def _extract_via_regex(ts_file: Path, repo_root: Path) -> dict:
    """Regex-based extraction.  Best-effort; covers common Angular patterns."""
    try:
        source = ts_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"classes": [], "imports": [], "di_params": [], "exports": [],
                "template_url": None, "style_urls": []}

    file_dir = ts_file.parent

    def resolve_rel(raw_url: str) -> Optional[str]:
        if not raw_url or raw_url.startswith("http") or raw_url.startswith("~"):
            return None
        try:
            abs_p = (file_dir / raw_url).resolve()
            return str(abs_p.relative_to(repo_root)).replace("\\", "/")
        except Exception:
            return None

    classes = []
    template_url: Optional[str] = None
    style_urls: list[str] = []
    di_params: list[dict] = []

    # Find decorators and pair each with the next class declaration
    dec_iter = list(_RE_DECORATOR.finditer(source))
    class_iter = list(_RE_CLASS.finditer(source))

    for ci, cls_m in enumerate(class_iter):
        cls_name = cls_m.group("name")
        cls_start_line = source[: cls_m.start()].count("\n") + 1
        # Find the last decorator immediately before this class
        dec = None
        for dm in reversed(dec_iter):
            if dm.end() <= cls_m.start():
                dec = dm
                break

        decorator_name = dec.group("name") if dec else None
        dec_args_raw = dec.group("args") if dec else ""

        dec_obj = {}
        if dec_args_raw:
            # templateUrl
            tu_m = _RE_TEMPLATE_URL.search(dec_args_raw)
            if tu_m:
                dec_obj["templateUrl"] = tu_m.group("url")
            # styleUrls
            su_m = _RE_STYLE_URLS.search(dec_args_raw)
            if su_m:
                dec_obj["styleUrls"] = _RE_STYLE_URL_STR.findall(su_m.group("urls"))
            # selector
            sel_m = _RE_SELECTOR.search(dec_args_raw)
            if sel_m:
                dec_obj["selector"] = sel_m.group("selector")

        decorators = []
        if decorator_name:
            decorators.append({"name": decorator_name, "args": dec_obj})

        if "templateUrl" in dec_obj:
            rel = resolve_rel(dec_obj["templateUrl"])
            if rel:
                template_url = rel
        for su in dec_obj.get("styleUrls", []):
            rel = resolve_rel(su)
            if rel:
                style_urls.append(rel)

        # DI — find constructor inside the class body (approximate)
        cls_body_start = cls_m.end()
        # Estimate body end as the next class or EOF
        cls_body_end = class_iter[ci + 1].start() if ci + 1 < len(class_iter) else len(source)
        body = source[cls_body_start:cls_body_end]
        ctor_m = _RE_CTOR.search(body)
        if ctor_m:
            for pm in _RE_CTOR_PARAM.finditer(ctor_m.group("params")):
                di_params.append({
                    "class_name": cls_name,
                    "param_name": pm.group("pname"),
                    "type_name":  pm.group("type"),
                    "start_line": cls_start_line,
                })

        rel_path = str(ts_file.relative_to(repo_root)).replace("\\", "/")
        classes.append({
            "name":           cls_name,
            "qualified_name": f"{rel_path}::{cls_name}",
            "decorators":     decorators,
            "start_line":     cls_start_line,
            "end_line":       cls_start_line + body.count("\n"),
        })

    imports = []
    for im in _RE_IMPORT.finditer(source):
        from_module = im.group("module")
        start_line  = source[: im.start()].count("\n") + 1
        for name in im.group("names").split(","):
            name = name.strip().split(" as ")[0].strip()
            if name:
                imports.append({"name": name, "from_module": from_module,
                                "start_line": start_line})

    exports = []
    for m in _RE_EXPORT_CONST.finditer(source):
        exports.append({"name": m.group("name"), "kind": "const",
                        "start_line": source[: m.start()].count("\n") + 1})
    for m in _RE_EXPORT_FN.finditer(source):
        exports.append({"name": m.group("name"), "kind": "function",
                        "start_line": source[: m.start()].count("\n") + 1})

    return {
        "classes":      classes,
        "imports":      imports,
        "di_params":    di_params,
        "exports":      exports,
        "template_url": template_url,
        "style_urls":   style_urls,
    }


# ── Annotation enrichment ─────────────────────────────────────────────────


def _enrich_ts_annotations(dec_names: list, cls_data: dict) -> list:
    """Enrich TypeScript class annotations with searchable component metadata.

    Stores selector tokens so the ``s_ts`` signal in localization can match
    ticket keywords against the component's identity rather than just its
    decorator name ("Component").

    E.g. @Component({ selector: 'app-jato-header', ... })
         → ["Component", "selector:app-jato-header", "seltok:jato", "seltok:header"]
    """
    result = list(dec_names)
    for dec in cls_data.get("decorators", []):
        args = dec.get("args", {})
        selector = args.get("selector")
        if selector:
            result.append(f"selector:{selector}")
            # Split hyphen/underscore-separated selector tokens for keyword matching
            for tok in re.split(r"[-_]", selector.lower()):
                if len(tok) > 2:
                    result.append(f"seltok:{tok}")
    return result


def _build_parse_result(
    ts_file: Path,
    repo_root: Path,
    sha256: str,
    data: dict,
) -> ParseResult:
    rel_path = str(ts_file.relative_to(repo_root)).replace("\\", "/")
    file_record = FileRecord(
        path=rel_path,
        language="typescript",
        sha256=sha256,
        size_bytes=ts_file.stat().st_size,
        parse_ok=True,
    )

    symbols: list[Symbol] = []
    edges:   list[Edge]   = []

    # File-level symbol (needed as edge src for CONTAINS / IMPORTS).
    # MUST be inserted into `symbols` before any edge that uses file_sym_id as
    # src_id — otherwise SQLite's FK constraint fires with "FOREIGN KEY failed".
    file_sym_id = _make_id(rel_path, "file", rel_path)
    file_loc    = SourceLocation(path=rel_path, start_line=1, end_line=1)
    symbols.append(Symbol(
        id=file_sym_id,
        kind=SymbolKind.FILE,
        name=Path(rel_path).name,       # e.g. "jheader.component.ts"
        qualified_name=rel_path,
        location=file_loc,
    ))

    for cls in data.get("classes", []):
        cls_name = cls["name"]
        qname    = cls["qualified_name"]
        cls_id   = _make_id(rel_path, "class", qname)

        # Determine spring_stereotype from decorator name (reuses existing column)
        dec_names = [d["name"] for d in cls.get("decorators", [])]
        stereotype = dec_names[0] if dec_names else None

        loc = SourceLocation(
            path=rel_path,
            start_line=cls.get("start_line", 1),
            end_line=cls.get("end_line", cls.get("start_line", 1)),
        )
        sym = Symbol(
            id=cls_id,
            kind=SymbolKind.CLASS,
            name=cls_name,
            qualified_name=qname,
            location=loc,
            annotations=_enrich_ts_annotations(dec_names, cls),
            spring_stereotype=stereotype,
        )
        symbols.append(sym)

        # CONTAINS: file → class
        edges.append(Edge(
            kind=EdgeKind.CONTAINS,
            src_id=file_sym_id,
            dst_id=cls_id,
            dst_name=qname,
            location=loc,
        ))

        # ANNOTATED_BY: class → each decorator
        for dec in cls.get("decorators", []):
            dec_id = _make_id(rel_path, "decorator", dec["name"])
            edges.append(Edge(
                kind=EdgeKind.ANNOTATED_BY,
                src_id=cls_id,
                dst_id=None,   # decorator may not be a local symbol
                dst_name=dec["name"],
                location=loc,
            ))

        # TEMPLATE_OF: class → html template file
        template_url = data.get("template_url")
        if template_url:
            edges.append(Edge(
                kind=EdgeKind.TEMPLATE_OF,
                src_id=cls_id,
                dst_id=None,
                dst_name=template_url,
                location=loc,
            ))

        # STYLE_OF: class → scss/css style files
        for style_url in data.get("style_urls", []):
            edges.append(Edge(
                kind=EdgeKind.STYLE_OF,
                src_id=cls_id,
                dst_id=None,
                dst_name=style_url,
                location=loc,
            ))

    # HAS_TYPE: DI parameters (constructor injection)
    for di in data.get("di_params", []):
        owner_qname = f"{rel_path}::{di['class_name']}"
        owner_id    = _make_id(rel_path, "class", owner_qname)
        param_qname = f"{rel_path}::{di['class_name']}::ctor::{di['param_name']}"
        param_id    = _make_id(rel_path, "parameter", param_qname)
        loc = SourceLocation(path=rel_path, start_line=di.get("start_line", 1))
        symbols.append(Symbol(
            id=param_id,
            kind=SymbolKind.PARAMETER,
            name=di["param_name"],
            qualified_name=param_qname,
            location=loc,
            parent_id=owner_id,
        ))
        edges.append(Edge(
            kind=EdgeKind.HAS_TYPE,
            src_id=param_id,
            dst_id=None,
            dst_name=di["type_name"],
            location=loc,
        ))

    # IMPORTS: file → imported module (relative imports only, for cross-file graph)
    file_dir = ts_file.parent
    for imp in data.get("imports", []):
        mod = imp["from_module"]
        if not mod.startswith("."):
            # third-party — record as unresolved dst_name only
            edges.append(Edge(
                kind=EdgeKind.IMPORTS,
                src_id=file_sym_id,
                dst_id=None,
                dst_name=mod,
                location=SourceLocation(path=rel_path, start_line=imp.get("start_line", 1)),
            ))
        else:
            # Resolve relative path
            for ext in (".ts", ".tsx", ".js", ""):
                candidate = (file_dir / (mod + ext)).resolve()
                try:
                    rel_candidate = str(candidate.relative_to(repo_root)).replace("\\", "/")
                    edges.append(Edge(
                        kind=EdgeKind.IMPORTS,
                        src_id=file_sym_id,
                        dst_id=None,
                        dst_name=rel_candidate,
                        location=SourceLocation(path=rel_path, start_line=imp.get("start_line", 1)),
                    ))
                    break
                except ValueError:
                    pass

    # Top-level exported symbols
    for exp in data.get("exports", []):
        exp_id  = _make_id(rel_path, exp["kind"], exp["name"])
        exp_loc = SourceLocation(path=rel_path, start_line=exp.get("start_line", 1))
        kind_map = {"function": SymbolKind.METHOD, "const": SymbolKind.FIELD}
        symbols.append(Symbol(
            id=exp_id,
            kind=kind_map.get(exp["kind"], SymbolKind.FIELD),
            name=exp["name"],
            qualified_name=f"{rel_path}::{exp['name']}",
            location=exp_loc,
        ))
        edges.append(Edge(
            kind=EdgeKind.CONTAINS,
            src_id=file_sym_id,
            dst_id=exp_id,
            dst_name=exp["name"],
            location=exp_loc,
        ))

    return ParseResult(file=file_record, symbols=symbols, edges=edges)


# ── Public parser class ───────────────────────────────────────────────────


class TypeScriptParser:
    """Parse TypeScript source files into the repository symbol graph.

    Thread-safe (no shared mutable state between calls).
    """

    def __init__(self, *, cache_dir: Optional[Path] = None):
        """
        Args:
            cache_dir: Directory for SHA-256 JSON cache files.
                       Defaults to ``<repo_root>/.aviator`` resolved at parse time.
        """
        self._node_bin: Optional[str] = _find_node()
        self._cache_dir = cache_dir
        self._ts_lib: Optional[str] = None  # resolved lazily per repo

    def _ensure_ts_lib(self, repo_root: Path) -> Optional[str]:
        if self._ts_lib is None:
            self._ts_lib = _find_typescript_lib(repo_root)
        return self._ts_lib

    def parse_file(self, ts_file: Path, repo_root: Path) -> ParseResult:
        """Parse a single ``.ts`` file and return a :class:`ParseResult`.

        Falls back to regex extraction if Node.js or the typescript package
        are unavailable.
        """
        sha = _sha256(ts_file)

        # Cache lookup
        cache_base = self._cache_dir or (repo_root / ".aviator")
        cp = _cache_path(cache_base, sha)
        if cp.exists():
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                return _build_parse_result(ts_file, repo_root, sha, data)
            except Exception:
                pass  # corrupt cache — re-extract

        # Primary: Node.js TypeScript Compiler API
        data: Optional[dict] = None
        if self._node_bin and _EXTRACTOR_SCRIPT.exists():
            ts_lib = self._ensure_ts_lib(repo_root)
            data = _extract_via_node(ts_file, repo_root, self._node_bin, ts_lib)
            if data:
                logger.debug(f"  TS (Node AST): {ts_file.name} — "
                             f"{len(data['classes'])} classes, {len(data['imports'])} imports")

        # Fallback: regex
        if data is None:
            data = _extract_via_regex(ts_file, repo_root)
            logger.debug(f"  TS (regex fallback): {ts_file.name} — "
                         f"{len(data['classes'])} classes")

        # Persist to cache
        try:
            cp.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

        return _build_parse_result(ts_file, repo_root, sha, data)
