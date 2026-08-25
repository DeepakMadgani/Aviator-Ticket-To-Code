"""Orchestrates parsing a Java repository into a :class:`SqliteStore`.

Responsibilities:

- discover `.java` files (respecting common ignore folders)
- parse each file with :class:`JavaParser`
- persist files / symbols / edges in a single transaction
- scan pom.xml / application.yml for project metadata
- report :class:`IndexStats` for the CLI / UI

This module is intentionally synchronous and dependency-light. A future
incremental indexer will sit on top of this and only re-parse files whose
SHA-256 changed (or that appear in `git diff`).
"""

from __future__ import annotations

import re
import hashlib
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

from aviator_core.models import IndexStats
from aviator_core.parsers import JavaParser
from aviator_core.parsers.typescript_parser import TypeScriptParser
from aviator_core.parsers.python_parser import PythonParser
from aviator_core.storage import SqliteStore


# Folders that are never worth indexing.
_DEFAULT_IGNORES = {
    ".git", ".hg", ".svn", ".idea", ".vscode",
    "target", "build", "out", "bin", "dist",
    "node_modules", ".gradle", ".mvn",
    "generated", "generated-sources", "generated-test-sources",
}


def discover_java_files(repo_root: Path, ignore: Optional[set[str]] = None) -> list[Path]:
    """Walk `repo_root` and return every `.java` file outside ignored directories."""

    ignore = set(_DEFAULT_IGNORES) | (ignore or set())
    out: list[Path] = []
    for path in repo_root.rglob("*.java"):
        if any(part in ignore for part in path.relative_to(repo_root).parts[:-1]):
            continue
        out.append(path)
    return out


def discover_typescript_files(repo_root: Path, ignore: Optional[set[str]] = None) -> list[Path]:
    """Walk `repo_root` and return every `.ts` / `.tsx` file outside ignored directories.

    Excludes ``.d.ts`` declaration files (type stubs only, no runtime behaviour).
    """
    ignore = set(_DEFAULT_IGNORES) | (ignore or set())
    out: list[Path] = []
    for ext in ("*.ts", "*.tsx"):
        for path in repo_root.rglob(ext):
            if path.name.endswith(".d.ts"):
                continue
            parts = path.relative_to(repo_root).parts[:-1]
            if any(part in ignore for part in parts):
                continue
            out.append(path)
    return out


def discover_python_files(repo_root: Path, ignore: Optional[set[str]] = None) -> list[Path]:
    """Walk `repo_root` and return every `.py` file outside ignored directories."""
    ignore = set(_DEFAULT_IGNORES) | (ignore or set())
    out: list[Path] = []
    for path in repo_root.rglob("*.py"):
        if any(part in ignore for part in path.relative_to(repo_root).parts[:-1]):
            continue
        out.append(path)
    return out


def discover_template_files(repo_root: Path, ignore: Optional[set[str]] = None) -> list[Path]:
    """Walk `repo_root` and return every ``.html``, ``.scss``, ``.css`` file.

    These are registered as bare ``FileRecord`` rows (no symbol extraction) so
    that TEMPLATE_OF / STYLE_OF edges resolve to real file paths in the index.
    """
    ignore = set(_DEFAULT_IGNORES) | (ignore or set())
    out: list[Path] = []
    for ext in ("*.html", "*.scss", "*.css"):
        for path in repo_root.rglob(ext):
            parts = path.relative_to(repo_root).parts[:-1]
            if any(part in ignore for part in parts):
                continue
            out.append(path)
    return out


# ---------------------------------------------------------------------------
# Project metadata scanners
# ---------------------------------------------------------------------------

_MVN_NS = "http://maven.apache.org/POM/4.0.0"


def _mvn(tag: str) -> str:
    """Return `tag` with Maven namespace prefix."""
    return f"{{{_MVN_NS}}}{tag}"


def scan_pom_xml(pom_path: Path) -> dict[str, str]:
    """Extract metadata from a Maven pom.xml.

    Returns a dict with keys:
      artifact_id, group_id, version, java_version, spring_boot_version,
      build_system, dependencies (comma-separated groupId:artifactId list)
    """
    try:
        tree = ET.parse(pom_path)
    except ET.ParseError:
        return {"build_system": "maven"}

    root = tree.getroot()

    # Support both namespaced and non-namespaced pom.xml
    ns = _MVN_NS if root.tag.startswith("{") else ""

    def get(tag: str) -> Optional[str]:
        el = root.find(f"{{{ns}}}{tag}" if ns else tag) if ns else root.find(tag)
        return el.text.strip() if el is not None and el.text else None

    def get_ns(tag: str) -> Optional[str]:
        """Try namespaced first, then plain."""
        el = root.find(_mvn(tag))
        if el is None:
            el = root.find(tag)
        return el.text.strip() if el is not None and el.text else None

    result: dict[str, str] = {"build_system": "maven"}

    for key, tag in [("artifact_id", "artifactId"), ("group_id", "groupId"), ("version", "version")]:
        val = get_ns(tag)
        if val:
            result[key] = val

    # Java version — may be in properties
    props = root.find(_mvn("properties")) or root.find("properties")
    if props is not None:
        for java_tag in ("java.version", "maven.compiler.source", "maven.compiler.release"):
            el = props.find(_mvn(java_tag)) or props.find(java_tag)
            if el is not None and el.text:
                result["java_version"] = el.text.strip()
                break
        for sb_tag in ("spring-boot.version", "spring.boot.version"):
            el = props.find(_mvn(sb_tag)) or props.find(sb_tag)
            if el is not None and el.text:
                result["spring_boot_version"] = el.text.strip()
                break

    # Spring Boot from parent
    parent = root.find(_mvn("parent")) or root.find("parent")
    if parent is not None:
        gid_el = parent.find(_mvn("groupId")) or parent.find("groupId")
        aid_el = parent.find(_mvn("artifactId")) or parent.find("artifactId")
        ver_el = parent.find(_mvn("version")) or parent.find("version")
        if gid_el is not None and "spring-boot" in (gid_el.text or ""):
            if ver_el is not None and ver_el.text:
                result.setdefault("spring_boot_version", ver_el.text.strip())
        if aid_el is not None and aid_el.text:
            result["parent_artifact"] = aid_el.text.strip()

    # Dependencies (first 50 unique groupId:artifactId)
    deps_section = root.find(_mvn("dependencies")) or root.find("dependencies")
    if deps_section is not None:
        deps = []
        for dep in list(deps_section):
            gid = dep.find(_mvn("groupId")) or dep.find("groupId")
            aid = dep.find(_mvn("artifactId")) or dep.find("artifactId")
            if gid is not None and aid is not None and gid.text and aid.text:
                deps.append(f"{gid.text.strip()}:{aid.text.strip()}")
        if deps:
            result["dependencies"] = ", ".join(deps[:50])

    return result


def scan_application_yml(yml_path: Path) -> dict[str, str]:
    """Extract key application config from application.yml or application.properties.

    Intentionally avoids adding a PyYAML dependency — uses simple regex matching
    on the raw text for the most useful properties.
    """
    try:
        text = yml_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    result: dict[str, str] = {}
    is_properties = yml_path.suffix == ".properties"

    def _find(key_pattern: str) -> Optional[str]:
        if is_properties:
            m = re.search(rf"^\s*{re.escape(key_pattern)}\s*=\s*(.+)$", text, re.MULTILINE)
        else:
            # YAML: match "  key: value"
            leaf = key_pattern.split(".")[-1]
            m = re.search(rf"^\s*{re.escape(leaf)}\s*:\s*(.+)$", text, re.MULTILINE)
        return m.group(1).strip() if m else None

    for prop, config_key in [
        ("spring.application.name", "app_name"),
        ("server.port", "server_port"),
        ("spring.datasource.url", "datasource_url"),
        ("spring.datasource.driver-class-name", "datasource_driver"),
        ("spring.kafka.bootstrap-servers", "kafka_bootstrap"),
        ("spring.rabbitmq.host", "rabbitmq_host"),
        ("management.server.port", "actuator_port"),
    ]:
        val = _find(prop)
        if val:
            result[config_key] = val

    # Detect Kafka topics from any line containing "topic"
    topic_matches = re.findall(r'(?:topic[s]?)\s*[=:]\s*(\S+)', text, re.IGNORECASE)
    if topic_matches:
        result["kafka_topics"] = ", ".join(set(topic_matches[:20]))

    return result


def scan_project_metadata(repo_root: Path) -> list[tuple[str, str, str]]:
    """Scan pom.xml files and application config files in the repository.

    Returns a list of ``(key, value, source_file)`` tuples ready to be stored
    with :meth:`SqliteStore.upsert_project_config`.
    """
    records: list[tuple[str, str, str]] = []
    repo_root = repo_root.resolve()

    # Find root pom.xml and sub-module pom.xml files
    pom_files: list[Path] = []
    for pom in repo_root.rglob("pom.xml"):
        if any(part in _DEFAULT_IGNORES for part in pom.relative_to(repo_root).parts[:-1]):
            continue
        pom_files.append(pom)
    # Root pom first
    pom_files.sort(key=lambda p: len(p.parts))

    for pom in pom_files[:5]:  # max 5 pom files
        rel = pom.relative_to(repo_root).as_posix()
        data = scan_pom_xml(pom)
        for k, v in data.items():
            prefix = "" if pom == pom_files[0] else f"module.{pom.parent.name}."
            records.append((f"{prefix}{k}", v, rel))

    # application.yml / application.properties
    for yml in list(repo_root.rglob("application.yml")) + list(repo_root.rglob("application.properties")):
        if any(part in _DEFAULT_IGNORES for part in yml.relative_to(repo_root).parts[:-1]):
            continue
        rel = yml.relative_to(repo_root).as_posix()
        data = scan_application_yml(yml)
        for k, v in data.items():
            records.append((k, v, rel))

    return records


def _pkg_similarity(a: str, b: str) -> int:
    """Count matching leading package segments between two dotted package names."""
    pa = a.split(".")
    pb = b.split(".")
    score = 0
    for x, y in zip(pa, pb):
        if x == y:
            score += 1
        else:
            break
    return score


def resolve_call_edges(store: SqliteStore) -> int:
    """Resolve CALLS edges whose destination is still unresolved (dst_id=NULL).

    After all files are parsed every method symbol lives in SQLite.  We can now
    do a best-effort name lookup:

    * ``"validateUser"``         → single method in project → resolve directly
    * ``"areaService.create"``   → last segment ``"create"`` → prefer same package
    * ``"new AreaDto"``          → ``"AreaDto"`` → resolve to class symbol
    * ``"someVar.method()"``     → ``"method"`` → prefer same package

    Ambiguous / unresolvable calls stay unresolved (they become External nodes in
    Neo4j, which is fine — they represent third-party or unknown targets).

    Returns the number of edges that were successfully resolved.
    """
    import sqlite3 as _sqlite3

    conn = store._conn

    # Build in-memory lookup: simple_name → [(id, qualified_name, package)]
    # for methods, constructors, classes and interfaces.
    by_name: dict[str, list[dict]] = defaultdict(list)
    rows = conn.execute(
        "SELECT id, name, qualified_name, package, kind "
        "FROM symbols WHERE kind IN ('method','constructor','class','interface','enum')"
    ).fetchall()
    for row in rows:
        # Lower-case key so we can do case-insensitive match for "new AreaDto"
        by_name[row[1]].append({
            "id": row[0],
            "qname": row[2] or "",
            "pkg": row[3] or "",
            "kind": row[4],
        })

    # Pre-fetch caller packages so we can score candidates
    caller_pkg: dict[str, str] = {}
    src_rows = conn.execute(
        "SELECT id, package FROM symbols WHERE kind IN ('method','constructor')"
    ).fetchall()
    for r in src_rows:
        caller_pkg[r[0]] = r[1] or ""

    # Fetch all unresolved CALLS edges
    calls = conn.execute(
        "SELECT id, src_id, dst_name FROM edges "
        "WHERE kind = 'calls' AND dst_id IS NULL"
    ).fetchall()

    resolved = 0
    updates: list[tuple[str, int]] = []  # (dst_id, edge_rowid)

    for edge in calls:
        raw_name: str = edge[2]  # dst_name, e.g. "areaService.create" or "new AreaDto"

        # Strip "new " prefix for constructor/class calls
        if raw_name.startswith("new "):
            simple = raw_name[4:].strip()
            kinds_wanted = {"class", "constructor", "interface"}
        else:
            # last segment after the last ".", strip generic params "<...>"
            simple = raw_name.rsplit(".", 1)[-1]
            simple = re.sub(r"<.*>", "", simple).strip()
            kinds_wanted = {"method", "constructor"}

        if not simple:
            continue

        candidates = [c for c in by_name.get(simple, []) if c["kind"] in kinds_wanted]

        if not candidates:
            # Fallback: try without kind filter (catches cross-kind calls)
            candidates = by_name.get(simple, [])

        if not candidates:
            continue

        if len(candidates) == 1:
            dst_id = candidates[0]["id"]
        else:
            # Pick best by package similarity with caller
            src_pkg = caller_pkg.get(edge[1], "")
            best = max(candidates, key=lambda c: _pkg_similarity(src_pkg, c["pkg"]))
            dst_id = best["id"]

        updates.append((dst_id, edge[0]))

    if updates:
        conn.executemany(
            "UPDATE edges SET dst_id = ? WHERE id = ?",
            updates,
        )
        conn.commit()
        resolved = len(updates)

    return resolved


def index_repository(
    repo_root: Path,
    store: SqliteStore,
    *,
    progress: Optional[Callable[[Path, int, int], None]] = None,
) -> IndexStats:
    """Parse every Java file under `repo_root` and persist results into `store`.

    `progress` is invoked as `progress(current_file, done, total)` after each file.
    """

    repo_root = repo_root.resolve()
    files = discover_java_files(repo_root)
    stats = IndexStats(files_scanned=len(files))
    parser = JavaParser()
    started = time.monotonic()

    with store.transaction():
        # Phase A: scan project metadata from pom.xml / application.yml
        config_records = scan_project_metadata(repo_root)
        for key, value, source in config_records:
            store.upsert_project_config(key, value, source)

        # Phase B: parse Java files
        for i, path in enumerate(files, start=1):
            try:
                result = parser.parse_file(path, repo_root)
            except Exception as exc:  # pragma: no cover - defensive
                stats.files_failed += 1
                if progress:
                    progress(path, i, len(files))
                continue

            store.upsert_file(result.file)
            store.insert_symbols(result.symbols)
            store.insert_edges(result.edges)
            stats.symbols += len(result.symbols)
            stats.edges += len(result.edges)
            if result.file.parse_ok:
                stats.files_parsed += 1
            else:
                stats.files_failed += 1
            if progress:
                progress(path, i, len(files))

    # Phase C: resolve CALLS dst_id now that ALL symbols are indexed.
    # This turns method→External stubs into proper method→method edges in Neo4j.
    stats.resolved_calls = resolve_call_edges(store)

    # Phase D: index TypeScript / Angular / Python files.
    ts_files = discover_typescript_files(repo_root)
    template_files = discover_template_files(repo_root)
    py_files = discover_python_files(repo_root)
    stats.files_scanned += len(ts_files) + len(template_files) + len(py_files)
    ts_parser = TypeScriptParser(cache_dir=repo_root / ".aviator")
    py_parser = PythonParser()

    with store.transaction():
        # Register HTML/SCSS/CSS files as bare FileRecord rows so that
        # TEMPLATE_OF / STYLE_OF edges can resolve to real file paths.
        for tpl_path in template_files:
            sha = hashlib.sha256(tpl_path.read_bytes()).hexdigest()
            from aviator_core.models import FileRecord as _FR
            lang = (
                "html" if tpl_path.suffix == ".html"
                else "scss" if tpl_path.suffix in (".scss", ".sass")
                else "css"
            )
            store.upsert_file(_FR(
                path=str(tpl_path.relative_to(repo_root)).replace("\\", "/"),
                language=lang,
                sha256=sha,
                size_bytes=tpl_path.stat().st_size,
                parse_ok=True,
            ))

        # Parse TypeScript files with Compiler API (or regex fallback).
        for i, ts_path in enumerate(ts_files, start=1):
            try:
                result = ts_parser.parse_file(ts_path, repo_root)
            except Exception as exc:
                stats.files_failed += 1
                continue
            store.upsert_file(result.file)
            store.insert_symbols(result.symbols)
            store.insert_edges(result.edges)
            stats.symbols += len(result.symbols)
            stats.edges += len(result.edges)
            if result.file.parse_ok:
                stats.files_parsed += 1
            else:
                stats.files_failed += 1

        # Parse Python files with ast.NodeVisitor.
        for i, py_path in enumerate(py_files, start=1):
            try:
                result = py_parser.parse_file(py_path, repo_root)
            except Exception as exc:
                stats.files_failed += 1
                continue
            store.upsert_file(result.file)
            store.insert_symbols(result.symbols)
            store.insert_edges(result.edges)
            stats.symbols += len(result.symbols)
            stats.edges += len(result.edges)
            if result.file.parse_ok:
                stats.files_parsed += 1
            else:
                stats.files_failed += 1

    stats.duration_seconds = round(time.monotonic() - started, 3)
    return stats


def index_file(file_path, sqlite_store, neo4j_store=None, repo_root=None) -> dict:
    from aviator_core.parsers.java_parser import JavaParser
    from aviator_core.parsers.typescript_parser import TypeScriptParser
    from aviator_core.parsers.python_parser import PythonParser
    from pathlib import Path
    
    file_path = Path(file_path)
    repo_root = Path(repo_root) if repo_root else file_path.parent
    store = sqlite_store
    
    if str(file_path).endswith('.java'):
        parser = JavaParser()
        result = parser.parse_file(file_path, repo_root)
        store.upsert_file(result.file)
        store.insert_symbols(result.symbols)
        store.insert_edges(result.edges)
        return {'symbols': len(result.symbols)}
    elif str(file_path).endswith(('.ts', '.tsx', '.js', '.jsx')):
        parser = TypeScriptParser(cache_dir=repo_root / ".aviator")
        result = parser.parse_file(file_path, repo_root)
        store.upsert_file(result.file)
        store.insert_symbols(result.symbols)
        store.insert_edges(result.edges)
        return {'symbols': len(result.symbols)}
    elif str(file_path).endswith('.py'):
        parser = PythonParser()
        result = parser.parse_file(file_path, repo_root)
        store.upsert_file(result.file)
        store.insert_symbols(result.symbols)
        store.insert_edges(result.edges)
        return {'symbols': len(result.symbols)}
    return {}

