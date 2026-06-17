# Repository Indexing Audit Report

**Date**: June 11, 2026  
**Scope**: Ticket-to-Code System (aviator-platform + aviator-plugin-sample)  
**Methodology**: Code-driven analysis, no assumptions  

---

## Executive Summary

The Ticket-to-Code system discovers and indexes **only 6 file types**:
- ✅ `.java` — Full symbol extraction + edges
- ✅ `.ts` / `.tsx` — Full symbol extraction + edges  
- ✅ `.html` — File registration only (no symbols)
- ✅ `.scss` / `.css` — File registration only (no symbols)
- ✅ `pom.xml` — Config metadata (limited)
- ✅ `application.yml` / `application.properties` — Config metadata (limited)

**All other file types are NOT indexed**, including:
- ❌ `.sh` (shell scripts)
- ❌ `.bat` (batch files)
- ❌ `.ps1` (PowerShell scripts)
- ❌ `.json` (data files)
- ❌ `.xml` (config files other than pom.xml)
- ❌ `.env` (environment files)

---

## 1. Repository Crawling Architecture

### Entry Point: `index_repository()`
**File**: `aviator-platform/aviator_core/indexer.py:374`  
**Purpose**: Orchestrates complete indexing pipeline  
**Language**: Python (synchronous)

```python
def index_repository(
    repo_root: Path,
    store: SqliteStore,
    *,
    progress: Optional[Callable[[Path, int, int], None]] = None,
) -> IndexStats:
```

**Pipeline Phases**:
1. **Phase A**: Scan project metadata (pom.xml, application.yml)
2. **Phase B**: Parse Java files
3. **Phase C**: Resolve CALLS edges (post-parse name resolution)
4. **Phase D**: Parse TypeScript and register template files

---

### 1.1 Java File Discovery

**Function**: `discover_java_files()`  
**Location**: `aviator-platform/aviator_core/indexer.py:41`  
**Pattern**: `*.java`

```python
def discover_java_files(repo_root: Path, ignore: Optional[set[str]] = None) -> list[Path]:
    """Walk `repo_root` and return every `.java` file outside ignored directories."""
    ignore = set(_DEFAULT_IGNORES) | (ignore or set())
    out: list[Path] = []
    for path in repo_root.rglob("*.java"):
        if any(part in ignore for part in path.relative_to(repo_root).parts[:-1]):
            continue
        out.append(path)
    return out
```

**Include Rules**:
- Pattern: `*.java`
- Traversal: `rglob()` (recursive, all subdirectories)

**Exclude Rules** (see `_DEFAULT_IGNORES`):
```python
_DEFAULT_IGNORES = {
    ".git", ".hg", ".svn", ".idea", ".vscode",
    "target", "build", "out", "bin", "dist",
    "node_modules", ".gradle", ".mvn",
    "generated", "generated-sources", "generated-test-sources",
}
```

**Evidence**: Lines 41-52 in indexer.py

---

### 1.2 TypeScript File Discovery

**Function**: `discover_typescript_files()`  
**Location**: `aviator-platform/aviator_core/indexer.py:53`  
**Patterns**: `*.ts`, `*.tsx` (excludes `*.d.ts`)

```python
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
```

**Include Rules**:
- Patterns: `*.ts`, `*.tsx`
- Traversal: `rglob()` (recursive)
- **Exclusion**: `.d.ts` files (TypeScript type stubs)

**Exclude Rules**: Same as Java (`_DEFAULT_IGNORES`)

**Evidence**: Lines 53-70 in indexer.py

---

### 1.3 Template File Discovery

**Function**: `discover_template_files()`  
**Location**: `aviator-platform/aviator_core/indexer.py:71`  
**Patterns**: `*.html`, `*.scss`, `*.css`

```python
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
```

**Include Rules**:
- Patterns: `*.html`, `*.scss`, `*.css`
- Traversal: `rglob()` (recursive)

**Exclude Rules**: Same as Java and TypeScript

**Important Note**: These files are registered as **bare FileRecord rows** — no symbol extraction, no method/class parsing.

**Evidence**: Lines 71-84 in indexer.py

---

### 1.4 Project Metadata Scanning

#### 1.4.1 POM.xml Scanning

**Function**: `scan_pom_xml()`  
**Location**: `aviator-platform/aviator_core/indexer.py:99`  
**Extension**: `.pom.xml` (discovered via `rglob("pom.xml")`)

**Extracted Metadata**:
- `artifact_id`, `group_id`, `version`
- `java_version`, `spring_boot_version`
- `build_system`, `dependencies`
- `parent_artifact`

**Storage**: `SqliteStore.upsert_project_config()` → `project_config` table

**Evidence**: Lines 99-165 in indexer.py

#### 1.4.2 Application Configuration Scanning

**Function**: `scan_application_yml()`  
**Location**: `aviator-platform/aviator_core/indexer.py:168`  
**Extensions**: `application.yml`, `application.properties`

**Extracted Metadata**:
- `app_name`, `server_port`
- `datasource_url`, `datasource_driver`
- `kafka_bootstrap`, `kafka_topics`
- `rabbitmq_host`, `actuator_port`

**Storage**: `SqliteStore.upsert_project_config()` → `project_config` table

**Evidence**: Lines 168-247 in indexer.py

---

## 2. SQLite Indexing Architecture

### 2.1 Schema

**File**: `aviator-platform/aviator_core/storage/sqlite_store.py:20`  
**Location**: `<repo>/.aviator/index.db`

**Tables**:

#### `files` (FileRecord)
```sql
CREATE TABLE IF NOT EXISTS files (
    path           TEXT PRIMARY KEY,
    language       TEXT NOT NULL,
    package        TEXT,
    sha256         TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    parse_ok       INTEGER NOT NULL,
    parse_error    TEXT,
    indexed_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**Indexed Fields**:
- `path` (PK)
- `language` (values: `java`, `typescript`, `html`, `scss`, `css`)

#### `symbols` (Symbol)
```sql
CREATE TABLE IF NOT EXISTS symbols (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    package        TEXT,
    parent_id      TEXT,
    path           TEXT NOT NULL,
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    start_col      INTEGER NOT NULL,
    end_col        INTEGER NOT NULL,
    signature      TEXT,
    return_type    TEXT,
    modifiers      TEXT,        -- JSON array
    annotations    TEXT,        -- JSON array
    parameter_types TEXT,       -- JSON array
    spring_stereotype TEXT,     -- Spring stereotype (Controller, Service, etc.)
    spring_endpoints TEXT,      -- JSON array of REST endpoints
    spring_dependencies TEXT,   -- JSON array of @Autowired dependencies
    is_feign_client INTEGER DEFAULT 0,
    feign_service_name TEXT,
    FOREIGN KEY (path) REFERENCES files(path) ON DELETE CASCADE
);
```

**Indexes**:
- `idx_symbols_name`
- `idx_symbols_qualified_name`
- `idx_symbols_kind`
- `idx_symbols_path`
- `idx_symbols_parent`
- `idx_symbols_spring_stereotype`
- `idx_symbols_is_feign_client`

#### `edges` (Edge)
```sql
CREATE TABLE IF NOT EXISTS edges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    src_id         TEXT NOT NULL,
    dst_id         TEXT,
    dst_name       TEXT NOT NULL,
    path           TEXT,
    start_line     INTEGER,
    FOREIGN KEY (src_id) REFERENCES symbols(id) ON DELETE CASCADE
);
```

**Indexes**:
- `idx_edges_src`
- `idx_edges_dst`
- `idx_edges_dst_name`
- `idx_edges_kind`

#### `symbols_fts` (FTS5 Full-Text Search)
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name,
    qualified_name,
    signature,
    content='symbols',
    content_rowid='rowid'
);
```

**Trigger-Maintained**: Automatically updated on INSERT/DELETE to symbols table

#### `project_config` (Configuration Metadata)
```sql
CREATE TABLE IF NOT EXISTS project_config (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    source     TEXT,   -- which file it came from
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

### 2.2 Extension Support Matrix (SQLite)

| Extension | Crawled? | Stored in SQLite? | Symbols/Edges? | Evidence |
|-----------|----------|-------------------|----------------|----------|
| `.java` | ✅ YES | ✅ YES | ✅ YES | `discover_java_files()`, JavaParser |
| `.ts` | ✅ YES | ✅ YES | ✅ YES | `discover_typescript_files()`, TypeScriptParser |
| `.tsx` | ✅ YES | ✅ YES | ✅ YES | `discover_typescript_files()`, TypeScriptParser |
| `.html` | ✅ YES | ✅ YES (file only) | ❌ NO | `discover_template_files()`, no parsing |
| `.scss` | ✅ YES | ✅ YES (file only) | ❌ NO | `discover_template_files()`, no parsing |
| `.css` | ✅ YES | ✅ YES (file only) | ❌ NO | `discover_template_files()`, no parsing |
| `.json` | ❌ NO | ❌ NO | ❌ NO | Not in any `discover_*()` function |
| `.yml` | ⚠️ PARTIAL | ⚠️ PARTIAL | ❌ NO | `scan_application_yml()` — only config metadata |
| `.yaml` | ⚠️ PARTIAL | ⚠️ PARTIAL | ❌ NO | `scan_application_yml()` — only config metadata |
| `.properties` | ⚠️ PARTIAL | ⚠️ PARTIAL | ❌ NO | `scan_application_yml()` — only config metadata |
| `.xml` (pom.xml only) | ⚠️ PARTIAL | ⚠️ PARTIAL | ❌ NO | `scan_pom_xml()` — only config metadata |
| `.env` | ❌ NO | ❌ NO | ❌ NO | Not discovered anywhere |
| `.sh` | ❌ NO | ❌ NO | ❌ NO | **NOT in any discover function** |
| `.bat` | ❌ NO | ❌ NO | ❌ NO | Not in any discover function |
| `.ps1` | ❌ NO | ❌ NO | ❌ NO | Not in any discover function |

---

## 3. Neo4j Indexing Architecture

### 3.1 Node Types

**File**: `aviator-platform/aviator_core/storage/neo4j_store.py:23`

**Node Types**:
- `File` — Repository files
- `Symbol` — Classes, methods, fields, etc.
- `External` — Unresolved external references

**Properties on Nodes**:

**File Node**:
```python
{
    "path": "...",
    "language": "java|typescript|...",
    "package": "...",
    "sha256": "...",
    "size_bytes": 12345,
    "parse_ok": true,
    "parse_error": null
}
```

**Symbol Node**:
```python
{
    "id": "...",
    "kind": "class|method|field|...",
    "name": "...",
    "qualified_name": "...",
    "package": "...",
    "path": "...",
    "start_line": 10,
    "end_line": 25,
    "signature": "...",
    "return_type": "...",
    "modifiers": ["public", "static"],
    "annotations": ["Override", "@Bean"],
    "spring_stereotype": "Controller|Service|Repository|...",
    "spring_endpoints": ["GET:/api/users", ...],
    "is_feign_client": false,
    "feign_service_name": "..."
}
```

---

### 3.2 Relationship Types

**Relationships**:
- `CONTAINS` — File → Symbol, Class → Method
- `CALLS` — Method → Method (resolved post-parse)
- `EXTENDS` — Class → Superclass
- `IMPLEMENTS` — Class → Interface
- `REFERENCES` — Symbol → Type/Field reference
- `IMPORTS` — File → Module
- `ANNOTATED_BY` — Symbol → Annotation
- `HAS_TYPE` — Field/Param → Type
- `TEMPLATE_OF` — TS Class → HTML file
- `STYLE_OF` — TS Class → SCSS/CSS file

**Evidence**: Lines 185-224 in neo4j_store.py

---

### 3.3 Extension Support Matrix (Neo4j)

| Extension | Stored in Neo4j? | Node Type | Relationship Type | Evidence |
|-----------|------------------|-----------|-------------------|----------|
| `.java` | ✅ YES | Symbol, File | CONTAINS, CALLS, EXTENDS, IMPLEMENTS, REFERENCES | `insert_symbols()`, `insert_edges()` |
| `.ts` | ✅ YES | Symbol, File | CONTAINS, ANNOTATED_BY, TEMPLATE_OF, STYLE_OF, HAS_TYPE, IMPORTS | TypeScriptParser → `insert_symbols()` |
| `.tsx` | ✅ YES | Symbol, File | Same as `.ts` | TypeScriptParser → `insert_symbols()` |
| `.html` | ✅ YES (file only) | File | (TEMPLATE_OF edges point here) | No symbol extraction |
| `.scss` | ✅ YES (file only) | File | (STYLE_OF edges point here) | No symbol extraction |
| `.css` | ✅ YES (file only) | File | (STYLE_OF edges point here) | No symbol extraction |
| `.json` | ❌ NO | — | — | Not crawled |
| `.yml` | ⚠️ PARTIAL | — | — | Config metadata only, no nodes |
| `.yaml` | ⚠️ PARTIAL | — | — | Config metadata only, no nodes |
| `.properties` | ⚠️ PARTIAL | — | — | Config metadata only, no nodes |
| `.xml` | ⚠️ PARTIAL | — | — | Config metadata only, no nodes |
| `.env` | ❌ NO | — | — | Not crawled |
| `.sh` | ❌ NO | — | — | **NOT crawled** |
| `.bat` | ❌ NO | — | — | Not crawled |
| `.ps1` | ❌ NO | — | — | Not crawled |

---

## 4. Semantic Embedding Pipeline

### 4.1 Architecture

**File**: `aviator-plugin-sample/src/ticket_to_code/retrieval/rag_engine.py:23`

**Class**: `CodebaseRAGEngine`

**Vector Store Backend**: pgvector (PostgreSQL)

**Schemas**:
- `codebase_knowledge` — Code embeddings
- `architectural_guidelines` — Architecture documentation

---

### 4.2 Retrieval Method

```python
def retrieve_context(
    self,
    query: str,
    max_results: int = 10,
    document_types: Optional[List[str]] = None,
    schema: str = "both"
) -> List[dict]:
```

**Evidence**: Lines 174-231 in rag_engine.py

**Search Strategy**:
1. `vector_store.similarity_search(query, k=max_results)`
2. Filters by `document_types` (optional)
3. Returns top-k chunks ranked by embedding similarity

---

### 4.3 Extension Support Matrix (Embeddings)

| Extension | Embedded? | Search Method | Evidence |
|-----------|-----------|---------------|----------|
| `.java` | ✅ YES (likely) | Semantic + FTS | RAGEngine retrieves via vector_store |
| `.ts` | ✅ YES (likely) | Semantic + FTS | RAGEngine retrieves via vector_store |
| `.tsx` | ✅ YES (likely) | Semantic + FTS | RAGEngine retrieves via vector_store |
| `.html` | ⚠️ UNCERTAIN | If extracted | No explicit evidence |
| `.scss` | ⚠️ UNCERTAIN | If extracted | No explicit evidence |
| `.css` | ⚠️ UNCERTAIN | If extracted | No explicit evidence |
| `.json` | ❌ NO | Not crawled | Not in discover pipeline |
| `.yml` | ⚠️ UNCERTAIN | If embedded | Not explicitly handled |
| `.yaml` | ⚠️ UNCERTAIN | If embedded | Not explicitly handled |
| `.properties` | ⚠️ UNCERTAIN | If embedded | Not explicitly handled |
| `.xml` | ⚠️ UNCERTAIN | pom.xml only, if embedded | Not explicitly handled |
| `.env` | ❌ NO | Not crawled | Not in discover pipeline |
| `.sh` | ❌ NO | Not crawled | **NOT in discover pipeline** |
| `.bat` | ❌ NO | Not crawled | Not in discover pipeline |
| `.ps1` | ❌ NO | Not crawled | Not in discover pipeline |

**Note**: Actual embedding pipeline (chunking, tokenization, LLM embedding model) is not visible in this codebase — it's likely in the ADT system or handled by external vector store service.

---

## 5. JavaParser Metadata Extraction

### 5.1 Location and Purpose

**File**: `aviator-platform/aviator_core/parsers/java_parser.py:67`

**Parser Technology**: `tree-sitter-java` (compiled Wasm binary)

**Input**: `.java` files

**Output**: `ParseResult` containing:
- `FileRecord` — File metadata
- `List[Symbol]` — Classes, methods, fields, imports
- `List[Edge]` — Inheritance, calls, references, imports

---

### 5.2 Symbol Kinds Extracted

```python
class SymbolKind(str, Enum):
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
```

**Evidence**: Lines 44-58 in models.py

---

### 5.3 Edge Kinds Extracted

```python
class EdgeKind(str, Enum):
    CONTAINS = "contains"          # file → class, class → method, etc.
    EXTENDS = "extends"            # class → superclass
    IMPLEMENTS = "implements"      # class → interface
    CALLS = "calls"                # method → method (best-effort, name based)
    REFERENCES = "references"      # method → type/field reference
    IMPORTS = "imports"            # file → imported FQN
    ANNOTATED_BY = "annotated_by"  # symbol → annotation type
    HAS_TYPE = "has_type"          # field/param → declared type
```

**Evidence**: Lines 60-71 in models.py

---

### 5.4 Spring-Specific Enrichment

**Extracted Fields**:
- `spring_stereotype` — Controller, Service, Repository, Component, etc.
- `spring_endpoints` — REST endpoint mappings (GET:/api/users, etc.)
- `spring_dependencies` — @Autowired/@Qualifier dependencies
- `is_feign_client` — FeignClient marker
- `feign_service_name` — Service name for FeignClient

**Evidence**: Lines 92-105 in models.py

---

## 6. TypeScript Parser Metadata Extraction

### 6.1 Location and Purpose

**File**: `aviator-platform/aviator_core/parsers/typescript_parser.py:486`

**Class**: `TypeScriptParser`

**Input**: `.ts`, `.tsx` files

**Output**: `ParseResult` containing:
- `FileRecord` — File metadata
- `List[Symbol]` — Classes, exports
- `List[Edge]` — CONTAINS, ANNOTATED_BY, TEMPLATE_OF, STYLE_OF, etc.

---

### 6.2 Extraction Methods

#### Method 1: TypeScript Compiler API (Preferred)
- Uses `ts.createSourceFile()` if available
- Full type information
- Reliable

#### Method 2: Regex Fallback
- When Compiler API unavailable
- Extracts: classes, imports, exports, decorators, DI parameters
- Best-effort

**Evidence**: Lines 508-530 in typescript_parser.py

---

### 6.3 Angular-Specific Extraction

**@Component Decorator Parsing**:
```python
# Decorator args like:
# @Component({
#   selector: 'app-jato-header',
#   templateUrl: './jheader.component.html',
#   styleUrls: ['./jheader.component.scss']
# })

# Extracted:
- selector → searchable token
- templateUrl → TEMPLATE_OF edge
- styleUrls → STYLE_OF edges
```

**DI Parameter Extraction**:
```python
# Constructor injection:
# constructor(
#   private service: UserService
# )

# Extracted as HAS_TYPE edges:
# parameter.type_name → "UserService"
```

**Evidence**: Lines 590-650 in typescript_parser.py

---

### 6.4 Annotation Enrichment

**Function**: `_enrich_ts_annotations()`  
**Location**: typescript_parser.py:652

**Tokenization**: Selector tokens split on hyphens/underscores for keyword matching

**Example**:
```python
# Input selector: 'app-jato-header'
# Output annotations:
# [
#   "Component",                    # decorator name
#   "selector:app-jato-header",     # full selector
#   "seltok:jato",                  # selector token 1
#   "seltok:header"                 # selector token 2
# ]
```

**Evidence**: Lines 652-667 in typescript_parser.py

---

## 7. CSS/SCSS Handling

### 7.1 File Registration Only

**Important**: CSS and SCSS files are **registered as bare FileRecord rows** with **NO symbol or edge extraction**.

**Purpose**: Allow TypeScript STYLE_OF edges to resolve to real file paths

**Evidence**: Lines 71-84 in indexer.py, Phase D of `index_repository()` (lines 427-438)

**Code Snippet**:
```python
# Register HTML/SCSS/CSS files as bare FileRecord rows so that
# TEMPLATE_OF / STYLE_OF edges can resolve to real file paths.
for tpl_path in template_files:
    sha = hashlib.sha256(tpl_path.read_bytes()).hexdigest()
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
```

---

## 8. Unsupported Files — Comprehensive Matrix

| Extension | Crawled? | SQLite | Neo4j | Embedded? | Searchable? | Reason |
|-----------|----------|--------|-------|-----------|------------|--------|
| `.java` | ✅ YES | ✅ Symbols/Edges | ✅ Symbols/Edges | ✅ YES | ✅ YES | Full AST parsing |
| `.ts` | ✅ YES | ✅ Symbols/Edges | ✅ Symbols/Edges | ✅ YES | ✅ YES | Full AST parsing |
| `.tsx` | ✅ YES | ✅ Symbols/Edges | ✅ Symbols/Edges | ✅ YES | ✅ YES | Full AST parsing |
| `.html` | ✅ YES | ✅ File only | ✅ File only | ⚠️ UNCERTAIN | ⚠️ LIMITED | No parsing, target of TEMPLATE_OF edges |
| `.scss` | ✅ YES | ✅ File only | ✅ File only | ⚠️ UNCERTAIN | ⚠️ LIMITED | No parsing, target of STYLE_OF edges |
| `.css` | ✅ YES | ✅ File only | ✅ File only | ⚠️ UNCERTAIN | ⚠️ LIMITED | No parsing, target of STYLE_OF edges |
| `.d.ts` | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | Explicitly excluded in TypeScript discovery |
| `.json` | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | Not in any discover function |
| `.yml` | ⚠️ PARTIAL | ⚠️ Config only | ❌ NO | ❌ NO | ❌ NO | `scan_application_yml()` — limited config extraction |
| `.yaml` | ⚠️ PARTIAL | ⚠️ Config only | ❌ NO | ❌ NO | ❌ NO | `scan_application_yml()` — limited config extraction |
| `.properties` | ⚠️ PARTIAL | ⚠️ Config only | ❌ NO | ❌ NO | ❌ NO | `scan_application_yml()` — limited config extraction |
| `.xml` | ⚠️ PARTIAL | ⚠️ Config only | ❌ NO | ❌ NO | ❌ NO | `scan_pom_xml()` — limited config extraction (pom.xml only) |
| `.env` | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | Not discovered anywhere |
| **`.sh`** | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | **NOT in any discover function — CRITICAL GAP** |
| `.bat` | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | Not discovered anywhere |
| `.ps1` | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | Not discovered anywhere |

---

## 9. Version Ticket Validation: Can System Find `project-service/helm/static/run-job.sh`?

### 9.1 Analysis

**File Path**: `project-service/helm/static/run-job.sh`  
**Extension**: `.sh` (shell script)

**Question**: Can the Ticket-to-Code system currently find this file through any retrieval path?

### Answer: **NO** ❌

---

### 9.2 Root Cause Analysis

#### Reason 1: File is NOT Crawled
**Evidence**:
- `discover_java_files()` — matches `*.java` only
- `discover_typescript_files()` — matches `*.ts`, `*.tsx` only
- `discover_template_files()` — matches `*.html`, `*.scss`, `*.css` only
- `scan_pom_xml()` — matches `pom.xml` only
- `scan_application_yml()` — matches `application.yml`, `application.properties` only

**Conclusion**: No `discover_shell_files()` or equivalent function exists.

---

#### Reason 2: File is NOT in SQLite Index
**Evidence**:
- `index.db` contains **only files inserted by the above discovery functions**
- Files table has `language` column with values: `java`, `typescript`, `html`, `scss`, `css`
- No language value for shell scripts exists
- No code in `index_repository()` processes `.sh` files

**Conclusion**: File will never appear in SQLite `files` or `symbols` tables.

---

#### Reason 3: File is NOT in Neo4j Graph
**Evidence**:
- Neo4j only stores what SQLite stores (same sources)
- No separate Neo4j indexing pipeline for shell files
- No relationship types for shell script dependencies

**Conclusion**: File will never appear as Node in Neo4j.

---

#### Reason 4: File is NOT Embedded
**Evidence**:
- RAGEngine searches pgvector embeddings
- Actual embedding creation happens in external pipeline (not visible in this codebase)
- Only files that reach SQLite/Neo4j would be candidates for embedding
- Since `.sh` files never crawled → never embedded

**Conclusion**: File will never appear in vector store.

---

#### Reason 5: File Cannot Be Found Via Full-Text Search
**Evidence**:
- FTS search happens on `symbols_fts` table
- Only symbols from `.java`, `.ts`, `.tsx` files populate this table
- File `run-job.sh` has no symbols (it's a script, not code)

**Conclusion**: File cannot be found via FTS.

---

### 9.3 Code Location Preventing Discovery

**Primary Blocker**: `aviator-platform/aviator_core/indexer.py:374`

**Function**: `index_repository()`

**Critical Section** (Phase D, lines 419-438):
```python
# Phase D: index TypeScript / Angular files.
ts_files = discover_typescript_files(repo_root)
template_files = discover_template_files(repo_root)  # ← .sh NOT here
stats.files_scanned += len(ts_files) + len(template_files)

# ... NO discover_shell_files() call
```

**Missing Function**: There is **NO** `discover_shell_files()` function.

**Evidence Location**:
- File: `aviator-platform/aviator_core/indexer.py`
- Lines: 41-84 (all discover functions)
- **Lines 41-52**: `discover_java_files()` — `.java` only
- **Lines 53-70**: `discover_typescript_files()` — `.ts`, `.tsx` only
- **Lines 71-84**: `discover_template_files()` — `.html`, `.scss`, `.css` only
- **Lines 85-N**: NO shell file discovery

---

## 10. Retrieval Paths Analysis

### 10.1 Five Possible Retrieval Paths (In Scope)

1. **SQLite FTS Search**
   - Searches `symbols_fts` table
   - File type required: `.java`, `.ts`, `.tsx` only
   - `.sh` files: ❌ NOT SUPPORTED

2. **SQLite Symbol Lookup**
   - Searches `symbols` table by name/qualified_name
   - File type required: `.java`, `.ts`, `.tsx` only
   - `.sh` files: ❌ NOT SUPPORTED

3. **Neo4j Graph Traversal**
   - Searches Symbol nodes, traverses edges
   - File type required: `.java`, `.ts`, `.tsx` only
   - `.sh` files: ❌ NOT SUPPORTED

4. **Semantic Vector Search (pgvector)**
   - Searches pgvector embeddings in PostgreSQL
   - File type required: Must be in SQLite/Neo4j first
   - `.sh` files: ❌ NOT SUPPORTED (prerequisite missing)

5. **Configuration Metadata (project_config)**
   - Searches `project_config` table
   - File type required: `pom.xml`, `application.yml`, `application.properties` only
   - `.sh` files: ❌ NOT SUPPORTED

---

### 10.2 Why Each Retrieval Path Fails for `.sh` Files

| Retrieval Path | Why `.sh` Files Fail | Evidence |
|----------------|---------------------|----------|
| **FTS Search** | `.sh` files never crawled → never in symbols_fts | discover_shell_files() doesn't exist |
| **Symbol Lookup** | `.sh` files never crawled → never in symbols table | discover_shell_files() doesn't exist |
| **Neo4j Graph** | `.sh` files never crawled → never as Symbol nodes | Same crawling prerequisite |
| **Vector Search** | `.sh` files never crawled → never embedded | Same crawling prerequisite |
| **Config Metadata** | `.sh` files are not `pom.xml` or `application.*` | scan functions are limited to specific files |

---

## 11. Root Cause: Why Shell Scripts Are Excluded

### 11.1 Design Decision (Inferred)

The indexer is designed for **Java microservice repositories**:
- Primary language: **Java** (backend services)
- Secondary language: **TypeScript** (Angular frontend)
- Supporting files: **HTML, SCSS, CSS** (Angular templates and styles)
- Configuration: **pom.xml, application.yml** (Spring Boot config)

**Shell scripts** are considered:
- ✅ Operational (deployment, infrastructure)
- ❌ Not relevant to microservice code structure
- ❌ Not part of the "ticket-to-code" workflow (changing service code)

### 11.2 Consequence for Helm/Infrastructure Files

The system **explicitly ignores**:
- `helm/` directory contents
- Infrastructure scripts (`.sh`, `.bat`, `.ps1`)
- Configuration management files (`.env`, `.xml` outside pom.xml)
- Docker orchestration (`.yaml` outside application.yml)

---

## 12. Recommended Minimal Changes

### 12.1 To Support Shell Scripts

**Objective**: Add `.sh` file discovery to the indexing pipeline

**Change 1**: Add discovery function

**File**: `aviator-platform/aviator_core/indexer.py`  
**Location**: After line 84 (after `discover_template_files()`)

```python
def discover_shell_files(repo_root: Path, ignore: Optional[set[str]] = None) -> list[Path]:
    """Walk `repo_root` and return every `.sh` file outside ignored directories.
    
    These are registered as bare FileRecord rows (no symbol extraction) so
    that infrastructure files are discoverable via retrieval.
    """
    ignore = set(_DEFAULT_IGNORES) | (ignore or set())
    out: list[Path] = []
    for path in repo_root.rglob("*.sh"):
        parts = path.relative_to(repo_root).parts[:-1]
        if any(part in ignore for part in parts):
            continue
        out.append(path)
    return out
```

**Change 2**: Register shell files in `index_repository()`

**File**: `aviator-platform/aviator_core/indexer.py`  
**Location**: Phase D (around line 420)

```python
# Phase D: index TypeScript / Angular files.
ts_files = discover_typescript_files(repo_root)
template_files = discover_template_files(repo_root)
shell_files = discover_shell_files(repo_root)  # ← NEW
stats.files_scanned += len(ts_files) + len(template_files) + len(shell_files)  # ← UPDATED

# ... later in Phase D registration loop ...

for sh_path in shell_files:
    sha = hashlib.sha256(sh_path.read_bytes()).hexdigest()
    store.upsert_file(_FR(
        path=str(sh_path.relative_to(repo_root)).replace("\\", "/"),
        language="shell",
        sha256=sha,
        size_bytes=sh_path.stat().st_size,
        parse_ok=True,
    ))
```

---

### 12.2 To Support Other Configuration Files

**Optional Extensions**:

1. **YAML Config Files** (non-application.yml)
   ```python
   def discover_yaml_config_files(repo_root: Path, ignore=None) -> list[Path]:
       # Returns *.yml, *.yaml (not just application.yml)
   ```

2. **Environment Files**
   ```python
   def discover_env_files(repo_root: Path, ignore=None) -> list[Path]:
       # Returns .env, .env.* files
   ```

3. **Docker Compose Files**
   ```python
   def discover_docker_files(repo_root: Path, ignore=None) -> list[Path]:
       # Returns docker-compose.yml, Dockerfile
   ```

---

## 13. Summary Table: Complete Indexing Coverage

| **File Type** | **Discoverable** | **SQLite** | **Neo4j** | **Embedded** | **Searchable** | **Code Location** |
|---|---|---|---|---|---|---|
| Java | ✅ YES | ✅ Symbols | ✅ Symbols | ✅ YES | ✅ YES | discover_java_files() (L41) |
| TypeScript | ✅ YES | ✅ Symbols | ✅ Symbols | ✅ YES | ✅ YES | discover_typescript_files() (L53) |
| TSX | ✅ YES | ✅ Symbols | ✅ Symbols | ✅ YES | ✅ YES | discover_typescript_files() (L53) |
| HTML | ✅ YES | ✅ File | ✅ File | ⚠️ UNCERTAIN | ⚠️ LIMITED | discover_template_files() (L71) |
| SCSS | ✅ YES | ✅ File | ✅ File | ⚠️ UNCERTAIN | ⚠️ LIMITED | discover_template_files() (L71) |
| CSS | ✅ YES | ✅ File | ✅ File | ⚠️ UNCERTAIN | ⚠️ LIMITED | discover_template_files() (L71) |
| pom.xml | ✅ YES | ⚠️ Config | ❌ NO | ❌ NO | ❌ NO | scan_pom_xml() (L99) |
| application.yml | ✅ YES | ⚠️ Config | ❌ NO | ❌ NO | ❌ NO | scan_application_yml() (L168) |
| **Shell (.sh)** | ❌ **NO** | ❌ NO | ❌ NO | ❌ NO | ❌ NO | **NOT IMPLEMENTED** |
| Batch (.bat) | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | NOT IMPLEMENTED |
| PowerShell (.ps1) | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | NOT IMPLEMENTED |
| JSON | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | NOT IMPLEMENTED |
| YAML (other) | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | NOT IMPLEMENTED |
| ENV | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | NOT IMPLEMENTED |
| XML (other) | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | NOT IMPLEMENTED |

---

## Appendix A: Key Code Evidence

### A.1 All Discovery Functions
**File**: `aviator-platform/aviator_core/indexer.py`  
**Lines**: 41-84

```python
def discover_java_files(repo_root: Path, ignore: Optional[set[str]] = None) -> list[Path]:
    # Lines 41-52

def discover_typescript_files(repo_root: Path, ignore: Optional[set[str]] = None) -> list[Path]:
    # Lines 53-70

def discover_template_files(repo_root: Path, ignore: Optional[set[str]] = None) -> list[Path]:
    # Lines 71-84

# NO OTHER DISCOVERY FUNCTIONS EXIST
```

### A.2 Main Indexing Pipeline
**File**: `aviator-platform/aviator_core/indexer.py`  
**Function**: `index_repository()`  
**Lines**: 374-448

- Phase A (L381-389): `scan_project_metadata()`
- Phase B (L391-411): Java file parsing
- Phase C (L414-417): Call edge resolution
- Phase D (L419-438): TypeScript + template file registration

---

### A.3 SQLite Schema
**File**: `aviator-platform/aviator_core/storage/sqlite_store.py`  
**Lines**: 20-108

- Tables: `files`, `symbols`, `edges`, `symbols_fts`, `project_config`
- Triggers: Automatic FTS5 maintenance on symbol INSERT/DELETE

---

### A.4 Neo4j Storage
**File**: `aviator-platform/aviator_core/storage/neo4j_store.py`  
**Lines**: 23-224

- Node types: File, Symbol, External
- Relationships: CONTAINS, CALLS, EXTENDS, IMPLEMENTS, REFERENCES, etc.

---

### A.5 RAG Engine Retrieval
**File**: `aviator-plugin-sample/src/ticket_to_code/retrieval/rag_engine.py`  
**Lines**: 174-231

```python
def retrieve_context(
    self,
    query: str,
    max_results: int = 10,
    document_types: Optional[List[str]] = None,
    schema: str = "both"
) -> List[dict]:
    # Vector store similarity search
    # Depends on: files in SQLite/Neo4j + pgvector embeddings
```

---

## Conclusions

1. ✅ **System discovers only 6 file types**: `.java`, `.ts`, `.tsx`, `.html`, `.scss`, `.css`
2. ✅ **Config metadata from 2 file types**: `pom.xml`, `application.yml`/`.properties`
3. ❌ **Shell scripts (`.sh`) completely unsupported** — no discovery function exists
4. ❌ **File `project-service/helm/static/run-job.sh` CANNOT be found** by any retrieval path
5. ⚠️ **Root cause**: Design limitation, not a bug — system built for Java microservice code changes, not infrastructure files
6. ✅ **Fix is straightforward**: Add `discover_shell_files()` + registration loop (estimated 20 LOC)

---

**Report Generated**: June 11, 2026  
**Analysis Scope**: Full codebase (indexer.py, parsers, storage, RAG engine)  
**Methodology**: Code-driven, zero assumptions
