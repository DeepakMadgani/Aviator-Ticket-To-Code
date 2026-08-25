"""Python source file parser using the built-in `ast` module.

Extracts structural metadata from ``.py`` files and emits the same
``ParseResult`` shape as ``JavaParser``.

It extracts:
- Imports
- Classes
- Functions/Methods
- Top-level assignments (Fields/Variables)
- Function calls (Edges)
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from aviator_core.models import (
    Edge,
    EdgeKind,
    FileRecord,
    SourceLocation,
    Symbol,
    SymbolKind,
)
from aviator_core.parsers.java_parser import ParseResult


def _make_id(path: str, kind: str, qname: str) -> str:
    key = f"{path}:{kind}:{qname}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


class PythonAstVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self.current_parent_id: str | None = None
        self.current_qname_prefix: str = ""
        self.package: str = rel_path.replace("/", ".").replace(".py", "")
        
        # File symbol
        self.file_sym = Symbol(
            id=_make_id(self.rel_path, SymbolKind.FILE, self.rel_path),
            kind=SymbolKind.FILE,
            name=Path(self.rel_path).name,
            qualified_name=self.rel_path,
            package=self.package,
            location=SourceLocation(path=self.rel_path, start_line=1, end_line=1),
        )
        self.symbols.append(self.file_sym)

    def _add_symbol(self, kind: SymbolKind, name: str, node: ast.AST, qname: str | None = None, return_type: str | None = None) -> Symbol:
        if not qname:
            qname = f"{self.current_qname_prefix}.{name}" if self.current_qname_prefix else name
            
        sym_id = _make_id(self.rel_path, kind, qname)
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)
        
        sym = Symbol(
            id=sym_id,
            kind=kind,
            name=name,
            qualified_name=qname,
            package=self.package,
            parent_id=self.current_parent_id or self.file_sym.id,
            location=SourceLocation(
                path=self.rel_path,
                start_line=start_line,
                end_line=end_line,
            ),
            return_type=return_type,
        )
        self.symbols.append(sym)
        
        # Link to parent
        self.edges.append(Edge(
            kind=EdgeKind.CONTAINS,
            src_id=self.current_parent_id or self.file_sym.id,
            dst_id=sym_id,
            dst_name=name,
            location=sym.location
        ))
        
        return sym

    def _get_annotation_str(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Constant):
            return str(node.value)
        elif isinstance(node, ast.Subscript):
            value = self._get_annotation_str(node.value)
            slice_val = self._get_annotation_str(node.slice)
            return f"{value}[{slice_val}]"
        elif isinstance(node, ast.Attribute):
            val = self._get_annotation_str(node.value)
            return f"{val}.{node.attr}"
        return None

    def visit_ClassDef(self, node: ast.ClassDef):
        prev_parent = self.current_parent_id
        prev_prefix = self.current_qname_prefix
        
        sym = self._add_symbol(SymbolKind.CLASS, node.name, node)
        
        # Handle inheritance
        for base in node.bases:
            base_name = self._get_annotation_str(base)
            if base_name:
                self.edges.append(Edge(
                    kind=EdgeKind.EXTENDS,
                    src_id=sym.id,
                    dst_id="", # Handled by later resolution if possible
                    dst_name=base_name,
                    location=sym.location
                ))

        self.current_parent_id = sym.id
        self.current_qname_prefix = sym.qualified_name
        self.generic_visit(node)
        self.current_parent_id = prev_parent
        self.current_qname_prefix = prev_prefix

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_function(node)
        
    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        prev_parent = self.current_parent_id
        prev_prefix = self.current_qname_prefix
        
        ret_type = self._get_annotation_str(node.returns)
        sym = self._add_symbol(SymbolKind.METHOD, node.name, node, return_type=ret_type)
        
        # Handle decorators
        for dec in node.decorator_list:
            dec_name = self._get_annotation_str(dec)
            if dec_name:
                sym.annotations.append(dec_name)
                
        self.current_parent_id = sym.id
        self.current_qname_prefix = sym.qualified_name
        self.generic_visit(node)
        self.current_parent_id = prev_parent
        self.current_qname_prefix = prev_prefix

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            sym = self._add_symbol(SymbolKind.IMPORT, alias.name, node, qname=alias.name)
            self.edges.append(Edge(
                kind=EdgeKind.IMPORTS,
                src_id=self.file_sym.id,
                dst_id=sym.id,
                dst_name=alias.name,
                location=sym.location
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            qname = f"{module}.{alias.name}" if module else alias.name
            sym = self._add_symbol(SymbolKind.IMPORT, alias.name, node, qname=qname)
            self.edges.append(Edge(
                kind=EdgeKind.IMPORTS,
                src_id=self.file_sym.id,
                dst_id=sym.id,
                dst_name=qname,
                location=sym.location
            ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if self.current_parent_id:
            func_name = self._get_annotation_str(node.func)
            if func_name:
                self.edges.append(Edge(
                    kind=EdgeKind.CALLS,
                    src_id=self.current_parent_id,
                    dst_id="", # Resolved later
                    dst_name=func_name,
                    location=SourceLocation(
                        path=self.rel_path,
                        start_line=getattr(node, "lineno", 1),
                        end_line=getattr(node, "end_lineno", 1)
                    )
                ))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if isinstance(node.target, ast.Name) and self.current_parent_id == self.file_sym.id:
            var_type = self._get_annotation_str(node.annotation)
            self._add_symbol(SymbolKind.FIELD, node.target.id, node, return_type=var_type)
        self.generic_visit(node)
        
    def visit_Assign(self, node: ast.Assign):
        if self.current_parent_id == self.file_sym.id:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._add_symbol(SymbolKind.FIELD, target.id, node)
        self.generic_visit(node)


class PythonParser:
    """Parser for Python source files using standard ast library."""
    
    def parse_file(self, path: Path, repo_root: Path) -> ParseResult:
        rel_path = path.relative_to(repo_root).as_posix()
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raw = b""
            
        sha = hashlib.sha256(raw).hexdigest()
        file_record = FileRecord(
            path=rel_path,
            language="python",
            sha256=sha,
            size_bytes=len(raw),
        )
        result = ParseResult(file=file_record)
        
        try:
            source = raw.decode("utf-8")
            tree = ast.parse(source, filename=path.name)
        except Exception as exc:
            file_record.parse_ok = False
            file_record.parse_error = f"{type(exc).__name__}: {exc}"
            return result
            
        visitor = PythonAstVisitor(rel_path)
        visitor.visit(tree)
        
        result.symbols = visitor.symbols
        result.edges = visitor.edges
        return result
