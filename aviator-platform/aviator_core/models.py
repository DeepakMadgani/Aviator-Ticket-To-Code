"""Pydantic models that describe the structured repository memory.

Everything the parsers emit and the stores persist flows through these models.
They are deliberately small and orthogonal so we can later project them onto:

- SQLite tables (always-on default store)
- Neo4j nodes/edges (optional, opt-in via `[neo4j]` extra)
- Qdrant vectors (optional, opt-in via `[qdrant]` extra)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SymbolKind(str, Enum):
    """Kinds of structural elements we extract from source files."""

    FILE = "file"
    PACKAGE = "package"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    RECORD = "record"
    ANNOTATION_TYPE = "annotation_type"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    FIELD = "field"
    PARAMETER = "parameter"
    IMPORT = "import"


class EdgeKind(str, Enum):
    """Kinds of directed relationships between symbols."""

    CONTAINS = "contains"          # file → class, class → method, etc.
    EXTENDS = "extends"            # class → superclass
    IMPLEMENTS = "implements"      # class → interface
    CALLS = "calls"                # method → method (best-effort, name based)
    REFERENCES = "references"      # method → type/field reference
    IMPORTS = "imports"            # file → imported FQN
    ANNOTATED_BY = "annotated_by"  # symbol → annotation type
    HAS_TYPE = "has_type"          # field/param → declared type
    TEMPLATE_OF = "template_of"    # TS class → HTML template file
    STYLE_OF = "style_of"          # TS class → SCSS/CSS style file


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class SourceLocation(BaseModel):
    """1-based location range inside a source file."""

    path: str = Field(..., description="Repo-relative POSIX path.")
    start_line: int = 1
    start_col: int = 1
    end_line: int = 1
    end_col: int = 1


class Symbol(BaseModel):
    """A single structural element extracted from a Java source file.

    Symbols form the nodes of the dependency graph. The :attr:`id` is a stable,
    content-addressable string built from `(path, kind, qualified_name)` — it is
    what edges reference.
    """

    id: str
    kind: SymbolKind
    name: str
    qualified_name: str = Field(
        ..., description="Fully-qualified name when known (e.g. `com.acme.Foo#bar(int)`)."
    )
    package: Optional[str] = None
    parent_id: Optional[str] = None
    location: SourceLocation
    signature: Optional[str] = None
    modifiers: list[str] = Field(default_factory=list)
    annotations: list[str] = Field(default_factory=list)
    return_type: Optional[str] = None
    parameter_types: list[str] = Field(default_factory=list)
    doc: Optional[str] = None
    
    # Spring Framework intelligence
    spring_stereotype: Optional[str] = Field(
        None, description="Spring stereotype: Controller, Service, Repository, Component, etc."
    )
    spring_endpoints: list[str] = Field(
        default_factory=list, description="REST endpoints like GET:/api/users"
    )
    spring_dependencies: list[str] = Field(
        default_factory=list, description="@Autowired/@Qualifier dependency names"
    )
    is_feign_client: bool = Field(
        False, description="Whether this is a Feign client interface"
    )
    feign_service_name: Optional[str] = Field(
        None, description="Service name for @FeignClient"
    )

    model_config = {"frozen": False}


class Edge(BaseModel):
    """A directed relationship between two symbols (or a symbol and an FQN)."""

    kind: EdgeKind
    src_id: str
    # We allow `dst_id` to be missing when the target is an unresolved external
    # name (e.g. a method call on a type we did not index). `dst_name` always
    # carries the textual target so we never lose information.
    dst_id: Optional[str] = None
    dst_name: str
    location: Optional[SourceLocation] = None


class FileRecord(BaseModel):
    """Metadata about a single indexed source file."""

    path: str
    language: str = "java"
    package: Optional[str] = None
    sha256: str
    size_bytes: int
    parse_ok: bool = True
    parse_error: Optional[str] = None


class IndexStats(BaseModel):
    """Summary returned by the indexer for UI / CLI reporting."""

    files_scanned: int = 0
    files_parsed: int = 0
    files_failed: int = 0
    symbols: int = 0
    edges: int = 0
    resolved_calls: int = 0   # CALLS edges whose dst_id was resolved post-parse
    duration_seconds: float = 0.0
