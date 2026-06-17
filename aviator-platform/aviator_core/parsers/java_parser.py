"""Java source parser built on `tree-sitter-java`.

The parser extracts a structural view of each `.java` file:

- package and imports
- top-level + nested classes / interfaces / enums / records / annotation types
- fields, methods, constructors, parameters
- inheritance edges (`extends`, `implements`)
- best-effort call edges (`method_invocation` → callee name)
- annotation usage

It returns a :class:`ParseResult` containing :class:`Symbol` and :class:`Edge`
objects ready to be persisted by the storage layer. Symbol IDs are stable
hashes of `(path, kind, qualified_name, start_line)` so they survive minor
edits elsewhere in the file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tree_sitter import Language, Node, Parser
import tree_sitter_java as tsjava

from aviator_core.models import (
    Edge,
    EdgeKind,
    FileRecord,
    SourceLocation,
    Symbol,
    SymbolKind,
)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    """Bundle of everything a single `.java` file contributes to the index."""

    file: FileRecord
    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


# Node types that introduce a new type-level scope.
_TYPE_NODES = {
    "class_declaration": SymbolKind.CLASS,
    "interface_declaration": SymbolKind.INTERFACE,
    "enum_declaration": SymbolKind.ENUM,
    "record_declaration": SymbolKind.RECORD,
    "annotation_type_declaration": SymbolKind.ANNOTATION_TYPE,
}


class JavaParser:
    """Stateless wrapper around a configured `tree-sitter` Java parser."""

    def __init__(self) -> None:
        self._language = Language(tsjava.language())
        self._parser = Parser(self._language)

    # ------------------------------------------------------------------ public

    def parse_file(self, path: Path, repo_root: Path) -> ParseResult:
        """Parse a single file and return its structural contribution."""

        raw = path.read_bytes()
        rel_path = path.relative_to(repo_root).as_posix()
        sha = hashlib.sha256(raw).hexdigest()
        file_record = FileRecord(
            path=rel_path,
            language="java",
            sha256=sha,
            size_bytes=len(raw),
        )
        result = ParseResult(file=file_record)

        try:
            tree = self._parser.parse(raw)
        except Exception as exc:  # pragma: no cover - tree-sitter is very tolerant
            file_record.parse_ok = False
            file_record.parse_error = f"{type(exc).__name__}: {exc}"
            return result

        root = tree.root_node

        # File-level symbol so edges can point at the whole file.
        file_symbol = self._make_symbol(
            kind=SymbolKind.FILE,
            name=path.name,
            qualified_name=rel_path,
            node=root,
            rel_path=rel_path,
            parent_id=None,
        )
        result.symbols.append(file_symbol)

        package = self._extract_package(root, raw)
        file_record.package = package
        file_symbol.package = package

        # Imports as symbols + IMPORTS edges from file.
        for imp_name, imp_node in self._extract_imports(root, raw):
            imp_symbol = self._make_symbol(
                kind=SymbolKind.IMPORT,
                name=imp_name.split(".")[-1],
                qualified_name=imp_name,
                node=imp_node,
                rel_path=rel_path,
                parent_id=file_symbol.id,
            )
            result.symbols.append(imp_symbol)
            result.edges.append(
                Edge(
                    kind=EdgeKind.IMPORTS,
                    src_id=file_symbol.id,
                    dst_id=imp_symbol.id,
                    dst_name=imp_name,
                    location=imp_symbol.location,
                )
            )

        # Walk top-level type declarations.
        for child in root.named_children:
            if child.type in _TYPE_NODES:
                self._walk_type(
                    node=child,
                    raw=raw,
                    rel_path=rel_path,
                    package=package,
                    parent_qname=package or "",
                    parent_id=file_symbol.id,
                    result=result,
                )

        return result

    # ---------------------------------------------------------------- internals

    def _walk_type(
        self,
        node: Node,
        raw: bytes,
        rel_path: str,
        package: Optional[str],
        parent_qname: str,
        parent_id: str,
        result: ParseResult,
    ) -> None:
        """Process a class/interface/enum/record/annotation-type declaration."""

        kind = _TYPE_NODES[node.type]
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(name_node, raw)
        qname = f"{parent_qname}.{name}" if parent_qname else name

        modifiers, annotations = self._extract_modifiers_and_annotations(node, raw)
        
        # Extract Spring intelligence
        spring_stereotype = self._detect_spring_stereotype(annotations)
        is_feign, feign_service = self._detect_feign_client(node, raw, annotations)

        # Class-level @RequestMapping prefix (used to build full method routes)
        class_ann_args = self._extract_annotation_args(node, raw, self._MAPPING_ANNOTATIONS)
        class_mapping_prefix = class_ann_args.get("RequestMapping")

        type_symbol = self._make_symbol(
            kind=kind,
            name=name,
            qualified_name=qname,
            node=node,
            rel_path=rel_path,
            parent_id=parent_id,
            modifiers=modifiers,
            annotations=annotations,
            package=package,
            spring_stereotype=spring_stereotype,
            is_feign_client=is_feign,
            feign_service_name=feign_service,
        )
        result.symbols.append(type_symbol)
        result.edges.append(
            Edge(
                kind=EdgeKind.CONTAINS,
                src_id=parent_id,
                dst_id=type_symbol.id,
                dst_name=qname,
                location=type_symbol.location,
            )
        )
        for ann in annotations:
            result.edges.append(
                Edge(
                    kind=EdgeKind.ANNOTATED_BY,
                    src_id=type_symbol.id,
                    dst_name=ann,
                    location=type_symbol.location,
                )
            )

        # Inheritance edges.
        for superclass in self._extract_superclasses(node, raw):
            result.edges.append(
                Edge(
                    kind=EdgeKind.EXTENDS,
                    src_id=type_symbol.id,
                    dst_name=superclass,
                    location=type_symbol.location,
                )
            )
        for iface in self._extract_interfaces(node, raw):
            result.edges.append(
                Edge(
                    kind=EdgeKind.IMPLEMENTS,
                    src_id=type_symbol.id,
                    dst_name=iface,
                    location=type_symbol.location,
                )
            )

        # Body members.
        body = node.child_by_field_name("body")
        if body is None:
            return

        for member in body.named_children:
            if member.type in _TYPE_NODES:
                # Nested type.
                self._walk_type(
                    node=member,
                    raw=raw,
                    rel_path=rel_path,
                    package=package,
                    parent_qname=qname,
                    parent_id=type_symbol.id,
                    result=result,
                )
            elif member.type == "method_declaration":
                self._walk_method(
                    node=member,
                    raw=raw,
                    rel_path=rel_path,
                    owner_qname=qname,
                    owner_id=type_symbol.id,
                    is_constructor=False,
                    result=result,
                    class_mapping_prefix=class_mapping_prefix,
                )
            elif member.type == "constructor_declaration":
                self._walk_method(
                    node=member,
                    raw=raw,
                    rel_path=rel_path,
                    owner_qname=qname,
                    owner_id=type_symbol.id,
                    is_constructor=True,
                    result=result,
                    class_mapping_prefix=class_mapping_prefix,
                )
            elif member.type == "field_declaration":
                self._walk_field(
                    node=member,
                    raw=raw,
                    rel_path=rel_path,
                    owner_qname=qname,
                    owner_id=type_symbol.id,
                    result=result,
                )

    def _walk_method(
        self,
        node: Node,
        raw: bytes,
        rel_path: str,
        owner_qname: str,
        owner_id: str,
        is_constructor: bool,
        result: ParseResult,
        class_mapping_prefix: Optional[str] = None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(name_node, raw)
        return_type = None
        if not is_constructor:
            rt_node = node.child_by_field_name("type")
            if rt_node is not None:
                return_type = _text(rt_node, raw)
        params_node = node.child_by_field_name("parameters")
        param_types: list[str] = []
        param_names: list[str] = []
        if params_node is not None:
            for p in params_node.named_children:
                if p.type in ("formal_parameter", "spread_parameter"):
                    pt = p.child_by_field_name("type")
                    pn = p.child_by_field_name("name")
                    if pt is not None:
                        param_types.append(_text(pt, raw))
                    if pn is not None:
                        param_names.append(_text(pn, raw))

        signature = f"{name}({', '.join(param_types)})"
        qname = f"{owner_qname}#{signature}"
        modifiers, annotations = self._extract_modifiers_and_annotations(node, raw)
        
        # Extract Spring REST endpoints with actual path from annotation arguments
        method_ann_args = self._extract_annotation_args(node, raw, self._MAPPING_ANNOTATIONS)
        spring_endpoints = self._extract_rest_endpoints(
            annotations, name, method_ann_args, class_mapping_prefix
        )

        method_symbol = self._make_symbol(
            kind=SymbolKind.CONSTRUCTOR if is_constructor else SymbolKind.METHOD,
            name=name,
            qualified_name=qname,
            node=node,
            rel_path=rel_path,
            parent_id=owner_id,
            modifiers=modifiers,
            annotations=annotations,
            return_type=return_type,
            parameter_types=param_types,
            signature=signature,
            spring_endpoints=spring_endpoints,
        )
        result.symbols.append(method_symbol)
        result.edges.append(
            Edge(
                kind=EdgeKind.CONTAINS,
                src_id=owner_id,
                dst_id=method_symbol.id,
                dst_name=qname,
                location=method_symbol.location,
            )
        )
        for ann in annotations:
            result.edges.append(
                Edge(
                    kind=EdgeKind.ANNOTATED_BY,
                    src_id=method_symbol.id,
                    dst_name=ann,
                    location=method_symbol.location,
                )
            )

        # Parameters as nested symbols.
        if params_node is not None:
            for pname, ptype, pnode in self._iter_parameters(params_node, raw):
                p_symbol = self._make_symbol(
                    kind=SymbolKind.PARAMETER,
                    name=pname,
                    qualified_name=f"{qname}::{pname}",
                    node=pnode,
                    rel_path=rel_path,
                    parent_id=method_symbol.id,
                )
                result.symbols.append(p_symbol)
                if ptype:
                    result.edges.append(
                        Edge(
                            kind=EdgeKind.HAS_TYPE,
                            src_id=p_symbol.id,
                            dst_name=ptype,
                            location=p_symbol.location,
                        )
                    )

        # Best-effort call edges.
        body = node.child_by_field_name("body")
        if body is not None:
            for call_name, call_node in self._iter_calls(body, raw):
                result.edges.append(
                    Edge(
                        kind=EdgeKind.CALLS,
                        src_id=method_symbol.id,
                        dst_name=call_name,
                        location=_loc(call_node, rel_path),
                    )
                )

    def _walk_field(
        self,
        node: Node,
        raw: bytes,
        rel_path: str,
        owner_qname: str,
        owner_id: str,
        result: ParseResult,
    ) -> None:
        type_node = node.child_by_field_name("type")
        type_name = _text(type_node, raw) if type_node is not None else None
        modifiers, annotations = self._extract_modifiers_and_annotations(node, raw)
        
        # Extract Spring dependencies
        spring_dependencies = self._extract_spring_dependencies(annotations, type_name)

        # A single `field_declaration` may declare multiple variables.
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, raw)
            qname = f"{owner_qname}.{name}"
            field_symbol = self._make_symbol(
                kind=SymbolKind.FIELD,
                name=name,
                qualified_name=qname,
                node=declarator,
                rel_path=rel_path,
                parent_id=owner_id,
                modifiers=modifiers,
                annotations=annotations,
                return_type=type_name,
                spring_dependencies=spring_dependencies,
            )
            result.symbols.append(field_symbol)
            result.edges.append(
                Edge(
                    kind=EdgeKind.CONTAINS,
                    src_id=owner_id,
                    dst_id=field_symbol.id,
                    dst_name=qname,
                    location=field_symbol.location,
                )
            )
            if type_name:
                result.edges.append(
                    Edge(
                        kind=EdgeKind.HAS_TYPE,
                        src_id=field_symbol.id,
                        dst_name=type_name,
                        location=field_symbol.location,
                    )
                )
            for ann in annotations:
                result.edges.append(
                    Edge(
                        kind=EdgeKind.ANNOTATED_BY,
                        src_id=field_symbol.id,
                        dst_name=ann,
                        location=field_symbol.location,
                    )
                )

    # --------------------------------------------------------- small extractors

    @staticmethod
    def _extract_package(root: Node, raw: bytes) -> Optional[str]:
        for child in root.named_children:
            if child.type == "package_declaration":
                # First named child should be the dotted name.
                for n in child.named_children:
                    if n.type in ("scoped_identifier", "identifier"):
                        return _text(n, raw)
        return None

    @staticmethod
    def _extract_imports(root: Node, raw: bytes) -> list[tuple[str, Node]]:
        imports: list[tuple[str, Node]] = []
        for child in root.named_children:
            if child.type == "import_declaration":
                # Take the textual name (handles `import a.b.C;` and `import static a.b.C.*;`).
                name_node = None
                for n in child.named_children:
                    if n.type in ("scoped_identifier", "identifier"):
                        name_node = n
                        break
                if name_node is not None:
                    imports.append((_text(name_node, raw), child))
        return imports

    @staticmethod
    def _extract_modifiers_and_annotations(
        node: Node, raw: bytes
    ) -> tuple[list[str], list[str]]:
        modifiers: list[str] = []
        annotations: list[str] = []
        # The `modifiers` child holds keywords + annotations interleaved.
        for child in node.children:
            if child.type == "modifiers":
                for m in child.children:
                    if m.type == "marker_annotation" or m.type == "annotation":
                        name_node = m.child_by_field_name("name")
                        if name_node is not None:
                            annotations.append(_text(name_node, raw))
                    elif m.is_named is False and m.type.isalpha():
                        modifiers.append(m.type)
                    elif m.type in {
                        "public", "protected", "private", "static", "final",
                        "abstract", "synchronized", "native", "strictfp",
                        "default", "transient", "volatile", "sealed", "non-sealed",
                    }:
                        modifiers.append(m.type)
        return modifiers, annotations

    @staticmethod
    def _extract_superclasses(node: Node, raw: bytes) -> list[str]:
        out: list[str] = []
        sc = node.child_by_field_name("superclass")
        if sc is not None:
            for n in sc.named_children:
                if n.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                    out.append(_text(n, raw))
        return out

    @staticmethod
    def _extract_interfaces(node: Node, raw: bytes) -> list[str]:
        out: list[str] = []
        # `interfaces` field on class_declaration, `extends_interfaces` on interface_declaration.
        for field_name in ("interfaces", "extends_interfaces"):
            container = node.child_by_field_name(field_name)
            if container is None:
                continue
            # The container holds a `type_list` of one or more types.
            for n in container.named_children:
                if n.type == "type_list":
                    for t in n.named_children:
                        out.append(_text(t, raw))
                elif n.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                    out.append(_text(n, raw))
        return out

    @staticmethod
    def _iter_parameters(params_node: Node, raw: bytes):
        for p in params_node.named_children:
            if p.type not in ("formal_parameter", "spread_parameter"):
                continue
            ptype = p.child_by_field_name("type")
            pname = p.child_by_field_name("name")
            if pname is None:
                continue
            yield (
                _text(pname, raw),
                _text(ptype, raw) if ptype is not None else None,
                p,
            )

    def _iter_calls(self, body: Node, raw: bytes):
        """Yield (callee_name, node) for every method invocation in a body."""
        stack: list[Node] = [body]
        while stack:
            n = stack.pop()
            if n.type == "method_invocation":
                name_node = n.child_by_field_name("name")
                object_node = n.child_by_field_name("object")
                if name_node is not None:
                    callee = _text(name_node, raw)
                    if object_node is not None:
                        callee = f"{_text(object_node, raw)}.{callee}"
                    yield callee, n
            elif n.type == "object_creation_expression":
                t = n.child_by_field_name("type")
                if t is not None:
                    yield f"new {_text(t, raw)}", n
            stack.extend(n.children)

    # --------------------------------------------------------- Spring intelligence

    _MAPPING_ANNOTATIONS: set[str] = {
        "GetMapping", "PostMapping", "PutMapping", "DeleteMapping",
        "PatchMapping", "RequestMapping",
    }

    @staticmethod
    def _extract_annotation_args(node: Node, raw: bytes, target_names: set[str]) -> dict[str, Optional[str]]:
        """Extract first string argument from specific annotations on a node.

        Returns dict mapping annotation simple-name → path string (or None when
        annotation exists but no string literal argument could be found, which
        happens when a constant reference is used, e.g.
        ``@PostMapping(URLMappings.SOME_URL)``).
        """
        import re as _re
        result: dict[str, Optional[str]] = {}
        for child in node.children:
            if child.type != "modifiers":
                continue
            for m in child.children:
                if m.type not in ("annotation", "marker_annotation"):
                    continue
                name_node = m.child_by_field_name("name")
                if name_node is None:
                    continue
                ann_name = _text(name_node, raw).split(".")[-1]
                if ann_name not in target_names:
                    continue
                args = m.child_by_field_name("arguments")
                if args is None:
                    result[ann_name] = None
                    continue
                args_text = _text(args, raw)
                # Match string literals: ("path"), (value="path"), (path="path")
                match = _re.search(r'"([^"]*)"', args_text)
                result[ann_name] = match.group(1) if match else None
        return result

    @staticmethod
    def _detect_spring_stereotype(annotations: list[str]) -> Optional[str]:
        """Detect Spring stereotype from annotations."""
        stereotype_map = {
            "Controller": "Controller",
            "RestController": "RestController",
            "Service": "Service",
            "Repository": "Repository",
            "Component": "Component",
            "Configuration": "Configuration",
        }
        for ann in annotations:
            # Handle both simple names and FQN
            simple_name = ann.split(".")[-1]
            if simple_name in stereotype_map:
                return stereotype_map[simple_name]
        return None

    @staticmethod
    def _detect_feign_client(node: Node, raw: bytes, annotations: list[str]) -> tuple[bool, Optional[str]]:
        """Detect if this is a @FeignClient interface and extract service name."""
        for ann in annotations:
            simple_name = ann.split(".")[-1]
            if simple_name == "FeignClient":
                # Try to extract service name from annotation value
                for child in node.children:
                    if child.type == "modifiers":
                        for m in child.children:
                            if m.type == "annotation" or m.type == "marker_annotation":
                                name_node = m.child_by_field_name("name")
                                if name_node and "FeignClient" in _text(name_node, raw):
                                    # Look for arguments
                                    args = m.child_by_field_name("arguments")
                                    if args:
                                        text = _text(args, raw)
                                        # Extract simple patterns like (name="service-name") or ("service-name")
                                        import re
                                        match = re.search(r'(?:name\s*=\s*)?["\']([^"\']+)["\']', text)
                                        if match:
                                            return True, match.group(1)
                                    return True, None
        return False, None

    @staticmethod
    def _extract_rest_endpoints(
        annotations: list[str],
        method_name: str,
        ann_args: Optional[dict[str, Optional[str]]] = None,
        class_prefix: Optional[str] = None,
    ) -> list[str]:
        """Extract REST endpoints from Spring MVC/WebFlux annotations.

        *ann_args* maps annotation simple-name → extracted path string (``None``
        when the annotation was present but the path is a constant reference and
        therefore unresolvable at parse time).
        *class_prefix* is the path from a class-level ``@RequestMapping``.
        """
        if ann_args is None:
            ann_args = {}
        endpoints = []
        mapping_annotations = {
            "GetMapping": "GET",
            "PostMapping": "POST",
            "PutMapping": "PUT",
            "DeleteMapping": "DELETE",
            "PatchMapping": "PATCH",
            "RequestMapping": "ANY",
        }
        prefix = (class_prefix or "").rstrip("/")
        for ann in annotations:
            simple_name = ann.split(".")[-1]
            if simple_name not in mapping_annotations:
                continue
            http_method = mapping_annotations[simple_name]
            raw_path = ann_args.get(simple_name)
            if raw_path is not None:
                # Real string literal found — use it
                path = raw_path if raw_path.startswith("/") else f"/{raw_path}"
            else:
                # Constant reference or marker annotation — fall back to method name
                path = f"/{method_name}"
            full_path = f"{prefix}{path}" if prefix else path
            endpoints.append(f"{http_method}:{full_path}")
        return endpoints

    @staticmethod
    def _extract_spring_dependencies(annotations: list[str], type_name: Optional[str]) -> list[str]:
        """Extract Spring dependency injection metadata."""
        dependencies = []
        for ann in annotations:
            simple_name = ann.split(".")[-1]
            if simple_name in ("Autowired", "Inject"):
                if type_name:
                    dependencies.append(type_name)
            elif simple_name == "Qualifier":
                # TODO: Extract qualifier value
                dependencies.append("qualified")
        return dependencies

    # ------------------------------------------------------------------ helper

    def _make_symbol(
        self,
        *,
        kind: SymbolKind,
        name: str,
        qualified_name: str,
        node: Node,
        rel_path: str,
        parent_id: Optional[str],
        modifiers: Optional[list[str]] = None,
        annotations: Optional[list[str]] = None,
        return_type: Optional[str] = None,
        parameter_types: Optional[list[str]] = None,
        signature: Optional[str] = None,
        package: Optional[str] = None,
        spring_stereotype: Optional[str] = None,
        spring_endpoints: Optional[list[str]] = None,
        spring_dependencies: Optional[list[str]] = None,
        is_feign_client: bool = False,
        feign_service_name: Optional[str] = None,
    ) -> Symbol:
        loc = _loc(node, rel_path)
        sid = _symbol_id(rel_path, kind, qualified_name, loc.start_line)
        return Symbol(
            id=sid,
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            package=package,
            parent_id=parent_id,
            location=loc,
            signature=signature,
            modifiers=modifiers or [],
            annotations=annotations or [],
            return_type=return_type,
            parameter_types=parameter_types or [],
            spring_stereotype=spring_stereotype,
            spring_endpoints=spring_endpoints or [],
            spring_dependencies=spring_dependencies or [],
            is_feign_client=is_feign_client,
            feign_service_name=feign_service_name,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _text(node: Node, raw: bytes) -> str:
    return raw[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _loc(node: Node, rel_path: str) -> SourceLocation:
    sr, sc = node.start_point
    er, ec = node.end_point
    return SourceLocation(
        path=rel_path,
        start_line=sr + 1,
        start_col=sc + 1,
        end_line=er + 1,
        end_col=ec + 1,
    )


def _symbol_id(path: str, kind: SymbolKind, qname: str, line: int) -> str:
    h = hashlib.sha1(f"{path}|{kind.value}|{qname}|{line}".encode("utf-8")).hexdigest()
    return h[:16]
