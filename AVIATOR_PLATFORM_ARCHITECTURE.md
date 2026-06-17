# Aviator Platform — Enterprise Autonomous Software Engineering System

**Architecture & Implementation Roadmap**

---

## 📋 Table of Contents

1. [Core Philosophy](#core-philosophy)
2. [System Overview](#system-overview)
3. [Phase 1: Repository Intelligence Core](#phase-1-repository-intelligence-core-completed)
4. [Phase 2: Hybrid Localization Engine](#phase-2-hybrid-localization-engine-most-critical)
5. [Phase 3: Patch Generation Engine](#phase-3-patch-generation-engine)
6. [Phase 4: Validation Infrastructure](#phase-4-validation-infrastructure)
7. [Phase 5: LangGraph Workflow Orchestration](#phase-5-langgraph-workflow-orchestration)
8. [Phase 6: Transparent Engineering UI](#phase-6-transparent-engineering-ui)
9. [Phase 7: Production Features](#phase-7-production-features)
10. [Technology Stack](#technology-stack)
11. [Execution Flow Intelligence](#execution-flow-intelligence)
12. [Blast-Radius Analysis](#blast-radius-analysis)
13. [Backend Architecture](#backend-architecture)
14. [Agent System](#agent-system)
15. [Database Layer](#database-layer)
16. [Infrastructure](#infrastructure)

---

## 🎯 Core Philosophy

This platform is **NOT a simple AI code generator**. It is:

**Repository Intelligence Platform + Patch Safety System + Validation Infrastructure**

The LLM is ONLY the reasoning layer.

Core capabilities:
- **Repository Intelligence System** — understands existing code structure via AST + graph
- **Architecture Understanding System** — respects existing patterns and layers
- **AST-safe Patch Generation Engine** — modifies code minimally and safely
- **Runtime Validation System** — verifies changes work before human review
- **Human-in-the-loop Engineering Workspace** — transparent and controllable

### Engineering Principles

1. **AST-FIRST ARCHITECTURE** — Parse code into structured representations
2. **PATCH-FIRST MODIFICATION** — Modify existing code, don't rewrite files
3. **REPOSITORY-INTELLIGENCE-FIRST** — Understand before generating
4. **MINIMAL CODE MODIFICATION** — Change only what's necessary
5. **ARCHITECTURE-AWARE EDITING** — Preserve existing patterns
6. **HUMAN-OBSERVABLE EXECUTION** — Show all workflow steps
7. **VALIDATION BEFORE TRUST** — Test every change
8. **INCREMENTAL CONTEXT EXPANSION** — Load context intelligently

### Primary Objective

**Correct localization is MORE important than code generation quality.**

```
Ticket
  ↓
Repository Localization ← MOST CRITICAL PHASE
  ↓
Architecture Understanding
  ↓
Dependency Expansion
  ↓
Patch Planning
  ↓
Minimal Safe Modification
  ↓
Validation
  ↓
Human Review
```

---

## 🏗️ System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend (Next.js / React)                │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Project Panel  │  Chatbot Interface  │  Workflow   │   │
│  │  - Add project  │  - Ticket input     │  - Phase    │   │
│  │  - Select KB    │  - Chat history     │  - Files    │   │
│  │                 │  - Agent response   │  - Graph    │   │
│  └─────────────────────────────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST + WebSocket
┌───────────────────────────┴─────────────────────────────────┐
│               Backend (FastAPI + LangGraph)                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Workflow Orchestration (LangGraph Nodes)            │  │
│  │  1. Issue Classification                              │  │
│  │  2. Repository Localization (Hybrid Retrieval)       │  │
│  │  3. Dependency Expansion                              │  │
│  │  4. Patch Planning                                    │  │
│  │  5. Code Generation                                   │  │
│  │  6. Validation (Tests + Playwright)                   │  │
│  │  7. Human Review                                      │  │
│  └──────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────┴─────────────────────────────────┐
│        Repository Intelligence Core (aviator-platform)       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  AST Parser (tree-sitter-java)                       │  │
│  │  ↓                                                     │  │
│  │  Symbol Indexer (classes, methods, fields, calls)    │  │
│  │  ↓                                                     │  │
│  │  Storage Layer (SQLite + FTS5)                        │  │
│  │  ↓                                                     │  │
│  │  Query Engine (search, neighbors, callers)           │  │
│  └──────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────┴─────────────────────────────────┐
│                    Database Layer                            │
│  ┌─────────────┬──────────────┬──────────────┬──────────┐  │
│  │   SQLite    │    Neo4j     │   Qdrant     │  RAG KB  │  │
│  │  (symbols)  │ (graph rel.) │ (semantic)   │ (domain) │  │
│  └─────────────┴──────────────┴──────────────┴──────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 📦 Phase 1: Repository Intelligence Core **[COMPLETED]**

**Goal:** Parse Java repositories into queryable structural graphs.

### Components Built

#### 1. AST Parser (`aviator_core/parsers/java_parser.py`)
- **Technology:** tree-sitter-java (Python binding, no JVM)
- **Extracts:**
  - Packages and imports
  - Classes, interfaces, enums, records, annotation types
  - Methods, constructors, fields, parameters
  - Modifiers (public, static, final, etc.)
  - Annotations (@Service, @Override, etc.)
  - Inheritance edges (extends, implements)
  - Best-effort call edges (method invocations)

#### 2. Data Models (`aviator_core/models.py`)
```python
Symbol:
  - id: str (stable hash of path + kind + qname + line)
  - kind: SymbolKind (FILE, CLASS, METHOD, FIELD, etc.)
  - name: str
  - qualified_name: str (e.g., "com.acme.UserService#saveUser(User)")
  - package: str
  - parent_id: str
  - location: SourceLocation (path, start_line, end_line, etc.)
  - signature: str (for methods)
  - modifiers: list[str]
  - annotations: list[str]
  - return_type: str
  - parameter_types: list[str]

Edge:
  - kind: EdgeKind (CONTAINS, EXTENDS, IMPLEMENTS, CALLS, etc.)
  - src_id: str
  - dst_id: str (optional)
  - dst_name: str
  - location: SourceLocation

FileRecord:
  - path: str
  - language: str
  - package: str
  - sha256: str
  - size_bytes: int
  - parse_ok: bool
  - parse_error: str
```

#### 3. SQLite Storage (`aviator_core/storage/sqlite_store.py`)
- **Schema:**
  - `files` — file metadata + SHA256 for change detection
  - `symbols` — all structural elements with JSON arrays
  - `edges` — relationships with kind + src/dst
  - `symbols_fts` — FTS5 virtual table for fast keyword search
- **Features:**
  - Foreign key cascades (re-indexing a file cleans old data)
  - WAL mode for concurrency
  - Hybrid search (FTS5 MATCH + LIKE fallback)

#### 4. Indexer (`aviator_core/indexer.py`)
- Discovers `.java` files (ignores `target/`, `build/`, `.git/`, etc.)
- Parses each file
- Persists in a single transaction
- Reports `IndexStats` (files scanned/parsed/failed, symbols, edges, duration)

#### 5. Query Engine (`aviator_core/query.py`)
- `find_symbols(text, kinds, limit)` — keyword + FTS5 search
- `neighbors(symbol_id, direction)` — adjacency (out/in/both)
- `callers(symbol_name, limit)` — find call sites

#### 6. CLI (`aviator_core/cli.py`)
```bash
aviator index <repo>         # Parse repository
aviator stats                # Show counts
aviator search <text>        # Search symbols
aviator show <id>            # Show symbol + edges
aviator callers <name>       # Find call sites
```

### Output
- **Storage:** `<repo>/.aviator/index.db`
- **Tests:** 3 passing smoke tests
- **Performance:** ~0.18s for 3 files in tests

### Current Limitations
- Java only (no C#, TypeScript yet)
- Best-effort call resolution (name-based, not type-aware)
- No incremental indexing (full repo re-parse)
- SQLite only (Neo4j/Qdrant optional but not wired)

---

## 🔍 Phase 2: Hybrid Localization Engine **[MOST CRITICAL]**

**Goal:** Correctly identify the exact files and methods that must be modified.

**This is THE most important phase. Correct localization is more important than generation quality.**

### Why This Phase is Critical

If localization fails, everything downstream fails:
- Wrong files → wasted generation
- Wrong methods → incorrect patches
- Wrong context → hallucinated code

**Localization quality determines system success.**

### Architecture

```
Ticket Query
     ↓
┌─────────────────────────────────────────────────────┐
│        HYBRID LOCALIZATION PIPELINE                 │
│   (AST + Graph are primary, vectors are secondary)  │
│                                                     │
│  1. Keyword Search (ripgrep / FTS5)                │
│     → Extract entity names from ticket             │
│                                                     │
│  2. Symbol Search (SQLite FTS5 index)              │
│     → Find classes, methods, fields by name        │
│                                                     │
│  3. AST Search (tree-sitter queries)               │
│     → Find patterns (annotations, inheritance)     │
│                                                     │
│  4. Dependency Traversal (graph-first)             │
│     → Follow CALLS, EXTENDS, IMPLEMENTS edges      │
│                                                     │
│  5. Execution Path Analysis                         │
│     → Controller → Service → Repository flow       │
│                                                     │
│  6. Semantic Search (Qdrant - OPTIONAL)            │
│     → Fallback only, not primary strategy          │
│                                                     │
│  7. Candidate File Ranking                          │
│     → Combine scores (keyword 30%, symbol 25%,     │
│       graph 25%, AST 15%, semantic 5%)             │
│                                                     │
│  8. Method Localization                             │
│     → Identify exact methods within files          │
│                                                     │
│  9. Blast-Radius Detection                          │
│     → Analyze impact before patching               │
│                                                     │
│  → Architecture Layer Detection                     │
│  → Human Review Checkpoint (validate files)        │
└─────────────────────────────────────────────────────┘
     ↓
Ranked File List + Method Targets + Impact Analysis
```

### Components to Build

#### 1. Retrieval Orchestrator (`aviator_core/retrieval/orchestrator.py`)
```python
class HybridRetriever:
    def localize(self, query: str, context: dict) -> LocalizationResult:
        # Execute all strategies in parallel
        keyword_hits = self.keyword_search(query)
        symbol_hits = self.symbol_search(query)
        ast_hits = self.ast_search(query)
        semantic_hits = self.semantic_search(query)
        graph_hits = self.graph_traverse(symbol_hits)
        
        # Merge + rank
        candidates = self.merge_and_rank(...)
        
        # Filter by architecture constraints
        final = self.architecture_filter(candidates, context)
        
        return LocalizationResult(files=final, confidence=...)
```

#### 2. Keyword Search (`aviator_core/retrieval/keyword.py`)
- **Backend:** ripgrep (fast file content search)
- **Queries:** Extract entity names from ticket (class names, method names, field names)

#### 3. Symbol Search (Already Built)
- Use `aviator_core.query.find_symbols`
- Search by kind (class, method, interface)

#### 4. AST Search (`aviator_core/retrieval/ast_search.py`)
- **Technology:** tree-sitter queries
- **Patterns:**
  - Find methods with specific annotations (`@PostMapping`, `@Transactional`)
  - Find classes implementing interface
  - Find fields of specific type

#### 5. Semantic Search (`aviator_core/retrieval/semantic.py`)
- **Backend:** Qdrant vector database
- **Embeddings:** sentence-transformers (e.g., `all-MiniLM-L6-v2`)
- **Index:** Symbol qualified names + docstrings + method bodies
- **Query:** Ticket description → vector → nearest neighbors

#### 6. Graph Traversal (`aviator_core/retrieval/graph.py`)
- **Backend:** Neo4j (optional, SQLite fallback)
- **Queries:**
  - "Which classes call this method?"
  - "Which services depend on this repository?"
  - "What's the execution path from controller to database?"

#### 7. Dependency Expander
- Given a symbol ID, expand to:
  - Parent class
  - All implementations of interface
  - All callers
  - All callees
  - All types referenced

#### 8. Ranking Algorithm
```python
# AST + Graph are PRIMARY. Vectors are SECONDARY.
score = (
    0.30 * keyword_match +
    0.25 * symbol_match +
    0.25 * graph_centrality +     # Neo4j traversal
    0.15 * ast_pattern_match +
    0.05 * semantic_similarity    # Qdrant FALLBACK ONLY
)
```

#### 9. Blast-Radius Analyzer
```python
class BlastRadiusAnalyzer:
    def analyze(self, target_symbol_id: str) -> ImpactReport:
        """Analyze what breaks if this symbol changes."""
        
        # Direct callers
        callers = graph.query(
            "MATCH (s)-[:CALLS]->(target {id: $id}) RETURN s",
            id=target_symbol_id
        )
        
        # Transitive dependencies (3 hops)
        dependents = graph.query(
            "MATCH (s)-[:CALLS|REFERENCES*1..3]->(target {id: $id}) "
            "RETURN DISTINCT s",
            id=target_symbol_id
        )
        
        # Affected tests
        tests = find_tests_covering(target_symbol_id)
        
        # API surface impact
        api_impact = check_api_surface(target_symbol_id)
        
        return ImpactReport(
            direct_callers=len(callers),
            transitive_dependents=len(dependents),
            affected_tests=tests,
            breaks_api=api_impact.is_breaking,
            risk_level="high" if api_impact.is_breaking else "medium"
        )
```

#### 10. Human Review Checkpoint
```python
# CRITICAL: Human validates localization BEFORE patching
def localization_review_checkpoint(
    candidates: list[FileCandidate]
) -> list[FileCandidate]:
    """Show retrieved files to human for validation."""
    
    display_ui(
        title="Localization Results",
        message="Review files before patch generation",
        files=candidates,
        actions=["Approve", "Add Files", "Remove Files", "Abort"]
    )
    
    return wait_for_human_approval()
```

### Deliverables
- `aviator localize <ticket-id>` — returns ranked file + method list
- Confidence threshold (only return files above 0.7)
- Architecture layer detection (controller vs service vs repository)
- Blast-radius report (impact analysis)
- Human review checkpoint before proceeding

---

## 🔧 Phase 3: Patch Generation Engine

**Goal:** Generate minimal, safe, AST-aware code modifications.

**Philosophy: This is NOT code generation. This is AST-safe patch generation.**

### Workflow Graph

```
┌─────────────────┐
│  Ticket Input   │
└────────┬────────┘
         ↓
┌─────────────────────────┐
│ 1. Issue Classification │ ← Classify ticket type (bug, feature, refactor)
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 2. Repository           │ ← Hybrid Retrieval (Phase 2)
│    Localization         │ ← Output: Candidate files with scores
└────────┬────────────────┘
         ↓
    [Human Review? ✋]      ← Optional: User can add/remove files
         ↓
┌─────────────────────────┐
│ 3. Dependency           │ ← Expand context (imports, callers, callees)
│    Expansion            │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 4. Architecture         │ ← Detect layer (controller, service, repository)
│    Understanding        │ ← Identify patterns (DI, transactions, etc.)
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 5. Patch Planning       │ ← Determine modification strategy
│                         │ ← "Modify method X in class Y"
└────────┬────────────────┘
         ↓
    [Human Review? ✋]      ← Show plan before code generation
         ↓
┌─────────────────────────┐
│ 6. Code Generation      │ ← Generate minimal diffs
│    (Patch-first)        │ ← Use ADT Aviator LLM
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 7. Apply Patch          │ ← tree-sitter range replacement
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 8. Build & Test         │ ← Run unit tests
└────────┬────────────────┘
         ↓
    ┌────┴────┐
    │ Failed? │
    └────┬────┘
         │ Yes
         ↓
┌─────────────────────────┐
│ 9. Failure Analysis     │ ← Parse error, replan
└────────┬────────────────┘
         │ (retry 3x max)
         ↓
    [Back to Step 6]
         │ No
         ↓
┌─────────────────────────┐
│ 10. Playwright          │ ← Runtime validation
│     Validation          │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 11. Code Quality Gates  │ ← SonarQube, ESLint, Checkstyle
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 12. Human Review        │ ← Show diff, screenshots, test results
│     & Approval          │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 13. Git Commit + PR     │
└─────────────────────────┘
```

### LangGraph Implementation

#### State Schema (`workflow/state.py`)
```python
class WorkflowState(TypedDict):
    ticket_id: str
    ticket_description: str
    ticket_type: str  # bug, feature, refactor
    
    # Localization phase
    localization_result: LocalizationResult
    candidate_files: list[FileCandidate]
    selected_files: list[str]
    
    # Context phase
    expanded_context: dict[str, str]  # path → content
    dependency_graph: dict
    architecture_layer: str
    
    # Planning phase
    modification_plan: ModificationPlan
    
    # Generation phase
    patches: list[Patch]
    applied_files: list[str]
    
    # Validation phase
    build_result: BuildResult
    test_results: list[TestResult]
    playwright_results: list[PlaywrightResult]
    quality_gates: QualityGateResult
    
    # Control flow
    current_node: str
    retry_count: int
    human_approved: bool
    errors: list[str]
```

#### Node Definitions (`workflow/nodes/`)
```python
# workflow/nodes/classify.py
async def classify_issue(state: WorkflowState) -> WorkflowState:
    """Use LLM to classify ticket type."""
    classification = await llm.classify(state["ticket_description"])
    state["ticket_type"] = classification
    return state

# workflow/nodes/localize.py
async def localize_changes(state: WorkflowState) -> WorkflowState:
    """Run hybrid retrieval."""
    retriever = HybridRetriever()
    result = retriever.localize(state["ticket_description"])
    state["localization_result"] = result
    state["candidate_files"] = result.files
    return state

# workflow/nodes/expand.py
async def expand_dependencies(state: WorkflowState) -> WorkflowState:
    """Load file contents + dependencies."""
    for file_path in state["selected_files"]:
        content = read_file(file_path)
        state["expanded_context"][file_path] = content
        # Add imports, callers, etc.
    return state

# workflow/nodes/plan.py
async def plan_modifications(state: WorkflowState) -> WorkflowState:
    """Generate modification plan."""
    plan = await llm.plan_patches(
        ticket=state["ticket_description"],
        context=state["expanded_context"],
        architecture=state["architecture_layer"]
    )
    state["modification_plan"] = plan
    return state

# workflow/nodes/generate.py
async def generate_code(state: WorkflowState) -> WorkflowState:
    """Generate patches."""
    patches = []
    for target in state["modification_plan"].targets:
        patch = await llm.generate_patch(
            file_path=target.file,
            method=target.method,
            intent=target.intent,
            context=state["expanded_context"]
        )
        patches.append(patch)
    state["patches"] = patches
    return state

# workflow/nodes/validate.py
async def validate_changes(state: WorkflowState) -> WorkflowState:
    """Run tests."""
    build_result = await run_build()
    test_results = await run_tests()
    state["build_result"] = build_result
    state["test_results"] = test_results
    return state
```

#### Conditional Edges
```python
def should_retry(state: WorkflowState) -> str:
    if state["build_result"].failed and state["retry_count"] < 3:
        return "retry"
    elif state["build_result"].failed:
        return "failed"
    else:
        return "continue"

def needs_human_review(state: WorkflowState) -> str:
    if state["confidence"] < 0.7:
        return "human_review"
    else:
        return "continue"
```

#### Graph Construction (`workflow/graph.py`)
```python
from langgraph.graph import StateGraph

graph = StateGraph(WorkflowState)

# Add nodes
graph.add_node("classify", classify_issue)
graph.add_node("localize", localize_changes)
graph.add_node("human_review_files", human_review_node)
graph.add_node("expand", expand_dependencies)
graph.add_node("plan", plan_modifications)
graph.add_node("human_review_plan", human_review_node)
graph.add_node("generate", generate_code)
graph.add_node("apply", apply_patches)
graph.add_node("validate", validate_changes)
graph.add_node("analyze_failure", analyze_failure)
graph.add_node("playwright", run_playwright)
graph.add_node("quality", run_quality_gates)
graph.add_node("final_review", human_final_review)

# Add edges
graph.add_edge("classify", "localize")
graph.add_conditional_edges(
    "localize",
    needs_human_review,
    {"human_review": "human_review_files", "continue": "expand"}
)
graph.add_edge("human_review_files", "expand")
graph.add_edge("expand", "plan")
graph.add_edge("plan", "human_review_plan")
graph.add_edge("human_review_plan", "generate")
graph.add_edge("generate", "apply")
graph.add_edge("apply", "validate")
graph.add_conditional_edges(
    "validate",
    should_retry,
    {"retry": "analyze_failure", "continue": "playwright", "failed": "final_review"}
)
graph.add_edge("analyze_failure", "generate")
graph.add_edge("playwright", "quality")
graph.add_edge("quality", "final_review")

# Set entry point
graph.set_entry_point("classify")

workflow = graph.compile()
```

### Integration with Backend

#### FastAPI Endpoint (`chatbot/backend/main.py`)
```python
@app.post("/api/workflow/start")
async def start_workflow(request: WorkflowRequest):
    """Start LangGraph workflow."""
    initial_state = WorkflowState(
        ticket_id=request.ticket_id,
        ticket_description=request.description,
        current_node="classify",
        retry_count=0,
        errors=[]
    )
    
    # Run workflow asynchronously
    task_id = str(uuid.uuid4())
    asyncio.create_task(run_workflow(task_id, initial_state))
    
    return {"task_id": task_id, "status": "started"}

@app.websocket("/ws/workflow/{task_id}")
async def workflow_updates(websocket: WebSocket, task_id: str):
    """Stream workflow progress."""
    await websocket.accept()
    
    async for event in workflow_event_stream(task_id):
        await websocket.send_json({
            "node": event.node,
            "status": event.status,
            "data": event.data,
            "timestamp": event.timestamp
        })
```

---

### Patch-First Philosophy

**CRITICAL DISTINCTION:**

❌ **DON'T SAY:** "Code Generation"  
✅ **DO SAY:** "AST-safe Patch Generation" or "Repository-aware Modification"

**DON'T:** Rewrite entire files  
**DO:** Generate precise diffs targeting specific methods/classes

**This is your biggest differentiator:**
```
Find exact method
      ↓
Patch exact method
```

### Technology

#### 1. Range-Based Modification
```python
# Use tree-sitter node ranges
method_node = find_method_node(tree, "saveUser")
old_text = source[method_node.start_byte:method_node.end_byte]
new_text = generate_new_method(old_text, intent)

patch = Patch(
    file_path="UserService.java",
    start_byte=method_node.start_byte,
    end_byte=method_node.end_byte,
    old_text=old_text,
    new_text=new_text,
    kind="method_modification"
)
```

#### 2. Patch Types
```python
class PatchKind(Enum):
    METHOD_MODIFICATION = "method_modification"
    METHOD_ADDITION = "method_addition"
    FIELD_ADDITION = "field_addition"
    IMPORT_ADDITION = "import_addition"
    ANNOTATION_ADDITION = "annotation_addition"
    PARAMETER_ADDITION = "parameter_addition"
```

#### 3. Safe Application
```python
def apply_patch(patch: Patch) -> ApplyResult:
    # Read file
    content = read_file(patch.file_path)
    
    # Verify old_text matches (avoid stale patches)
    actual = content[patch.start_byte:patch.end_byte]
    if actual != patch.old_text:
        return ApplyResult(success=False, error="Stale patch")
    
    # Apply replacement
    new_content = (
        content[:patch.start_byte] +
        patch.new_text +
        content[patch.end_byte:]
    )
    
    # Re-parse to verify syntax
    try:
        parser.parse(new_content.encode())
    except Exception as e:
        return ApplyResult(success=False, error=f"Syntax error: {e}")
    
    # Write back
    write_file(patch.file_path, new_content)
    return ApplyResult(success=True)
```

#### 4. Patch Templates
```python
# Example: Add logging to method
LOGGING_TEMPLATE = """
    logger.info("Entering {method_name} with params: {params}");
    try {{
        {original_body}
    }} catch (Exception e) {{
        logger.error("Error in {method_name}", e);
        throw e;
    }}
"""

# Example: Add transaction annotation
TRANSACTION_TEMPLATE = """
@Transactional
{original_method}
"""
```

### LLM Prompt Engineering for Patch Generation

**IMPORTANT:** Prompts must emphasize "patch" not "generate from scratch".

```python
PATCH_GENERATION_PROMPT = """
You are an AST-aware code patcher. Modify ONLY the specified method.

DO NOT generate code from scratch.
DO NOT rewrite the entire file.
Generate ONLY the new version of the target method.

File: {file_path}
Method: {method_signature}

Current implementation:
```java
{current_method}
```

Required change: {intent}

Constraints:
1. Preserve existing parameter names
2. Keep existing imports (do not add new ones unless necessary)
3. Maintain coding style (indentation, naming, etc.)
4. Preserve error handling patterns
5. Keep transaction boundaries

Output ONLY the new method implementation (including signature).
"""
```

---

## ✅ Phase 4: Validation Infrastructure

**Goal:** Automated testing at multiple levels before human review.

**Validation is MORE important than generation speed.**

### Validation Layers

#### STATIC VALIDATION (runs first, fast)

##### 1. Syntax Validation (Immediate)
- Re-parse with tree-sitter after patch application
- Fail fast if syntax broken

##### 2. Build Validation
```python
class BuildValidator:
    def validate(self, project_path: Path) -> BuildResult:
        # For Java/Maven
        result = subprocess.run(
            ["mvn", "compile", "-q"],
            cwd=project_path,
            capture_output=True
        )
        
        if result.returncode != 0:
            errors = parse_maven_errors(result.stderr)
            return BuildResult(success=False, errors=errors)
        
        return BuildResult(success=True)
```

##### 3. Unit Test Validation
```python
class TestValidator:
    def validate(self, project_path: Path) -> TestResult:
        result = subprocess.run(
            ["mvn", "test", "-Dtest=*Test"],
            cwd=project_path,
            capture_output=True
        )
        
        test_report = parse_surefire_report(project_path)
        
        return TestResult(
            success=result.returncode == 0,
            passed=test_report.passed,
            failed=test_report.failed,
            failures=test_report.failure_details
        )
```

##### 4. Code Quality Gates (Static Analysis)
```python
class QualityGateValidator:
    def validate_static(self, project_path: Path) -> QualityResult:
        results = []
        
        # Checkstyle (for Java)
        checkstyle_result = run_checkstyle(project_path)
        results.append(checkstyle_result)
        
        # ESLint (for TypeScript/JS)
        eslint_result = run_eslint(project_path)
        results.append(eslint_result)
        
        # Architecture rules
        arch_result = validate_architecture_rules(project_path)
        results.append(arch_result)
        
        return QualityResult(
            passed=all(r.passed for r in results),
            issues=results
        )
```

#### RUNTIME VALIDATION (runs after static passes)

##### 5. Playwright Runtime Validation
```python
# chatbot/backend/services/playwright_validator.py
class PlaywrightValidator:
    async def validate_workflow(
        self,
        app_url: str,
        workflow_script: str
    ) -> PlaywrightResult:
        """Run UI workflow validation."""
        
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(
                record_video_dir="./validation_videos"
            )
            page = await context.new_page()
            
            # Execute workflow
            try:
                await page.goto(app_url)
                
                # Execute steps from script
                steps = parse_workflow_script(workflow_script)
                for step in steps:
                    await execute_step(page, step)
                
                # Take screenshot
                screenshot = await page.screenshot()
                
                return PlaywrightResult(
                    success=True,
                    screenshot=screenshot,
                    video_path=context.videos[0].path()
                )
            except Exception as e:
                return PlaywrightResult(
                    success=False,
                    error=str(e),
                    screenshot=await page.screenshot()
                )
            finally:
                await browser.close()
```

##### 6. Integration Test Validation
```python
class IntegrationTestValidator:
    def validate(self, project_path: Path) -> TestResult:
        """Run integration tests against live dependencies."""
        result = subprocess.run(
            ["mvn", "verify", "-Pintegration"],
            cwd=project_path,
            capture_output=True
        )
        
        return TestResult(
            success=result.returncode == 0,
            details=parse_integration_report(project_path)
        )
```

##### 7. API Contract Validation
```python
class APIContractValidator:
    def validate(self, changes: list[Patch]) -> ContractResult:
        """Verify API contracts not broken."""
        
        # Check if any REST endpoints changed signature
        breaking_changes = []
        for patch in changes:
            if is_rest_endpoint(patch):
                old_sig = extract_endpoint_signature(patch.old_text)
                new_sig = extract_endpoint_signature(patch.new_text)
                if not compatible(old_sig, new_sig):
                    breaking_changes.append(patch)
        
        return ContractResult(
            is_breaking=len(breaking_changes) > 0,
            broken_endpoints=breaking_changes
        )
```

### Validation Pipeline Order

```
1. Syntax Check (fail fast)
     ↓
2. Build (fail fast)
     ↓
3. Unit Tests (fail fast)
     ↓
4. Static Analysis (Checkstyle, ESLint)
     ↓
5. Architecture Rules
     ↓
6. Integration Tests
     ↓
7. Playwright (UI workflows)
     ↓
8. API Contract Validation
     ↓
9. Code Coverage Check
```

### Failure Analysis Agent

```python
class FailureAnalyzer:
    async def analyze(
        self,
        build_result: BuildResult,
        test_result: TestResult
    ) -> FailureAnalysis:
        """Use LLM to diagnose failures and suggest fixes."""
        
        prompt = f"""
        Build failed with errors:
        {build_result.errors}
        
        Test failures:
        {test_result.failures}
        
        Analyze the root cause and suggest a fix.
        """
        
        analysis = await llm.analyze(prompt)
        
        return FailureAnalysis(
            root_cause=analysis.root_cause,
            suggested_fix=analysis.fix,
            confidence=analysis.confidence
        )
```

---

## 🤖 Phase 5: LangGraph Workflow Orchestration

**Goal:** Orchestrate the multi-stage pipeline with human-in-the-loop controls.

**IMPORTANT:** LangGraph orchestrates strong deterministic components.

It does NOT replace:
- Repository intelligence
- Localization quality
- Patch safety
- Validation rigor

**LangGraph should be lightweight orchestration, not agent swarm.**

### Workflow Graph (Corrected Order)

```
┌─────────────────┐
│  Ticket Input   │
└────────┬────────┘
         ↓
┌─────────────────────────┐
│ 1. Issue Classification │ ← LLM: bug vs feature vs refactor
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 2. Hybrid Localization  │ ← Phase 2 engine (AST+Graph primary)
│    (MOST CRITICAL)      │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 3. Blast-Radius         │ ← Impact analysis
│    Analysis             │
└────────┬────────────────┘
         ↓
    [Human Review ✋]       ← EARLY validation checkpoint
         ↓
┌─────────────────────────┐
│ 4. Dependency           │ ← Load file contents + imports
│    Expansion            │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 5. AST-safe Patch       │ ← Phase 3 engine (method-level diffs)
│    Generation           │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 6. Apply Patch          │ ← tree-sitter range replacement
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 7. Static Validation    │ ← Build + unit tests + lint
└────────┬────────────────┘
         ↓
    ┌────┴────┐
    │ Failed? │
    └────┬────┘
         │ Yes (retry 3x max)
         ↓
┌─────────────────────────┐
│ 8. Failure Analysis     │ ← Parse error, suggest fix
└────────┬────────────────┘
         │
         └──────> [Back to Step 5]
         │ No
         ↓
┌─────────────────────────┐
│ 9. Runtime Validation   │ ← Integration + Playwright
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 10. Quality Gates       │ ← Code quality checks
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 11. Human Approval      │ ← Show diff, tests, screenshots
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ 12. Git Commit + PR     │
└─────────────────────────┘
```

### Minimal Agent Architecture

**IMPORTANT:** Minimize agents. Prefer deterministic components.

```python
# WRONG: Too many agents
# agents = [
#     PlanningAgent(),
#     RetrievalAgent(),
#     ValidationAgent(),
#     AnalysisAgent(),
#     RefactoringAgent(),
#     TestGenerationAgent(),
#     ...
# ]

# RIGHT: Fewer, stronger components
components = {
    "localization": HybridLocalizer(),      # deterministic
    "patch_gen": PatchGenerator(),          # LLM-based
    "validation": ValidationPipeline(),     # deterministic
    "reasoning": LLMReasoner(),             # LLM-based
}
```

### LangGraph State Schema

```python
class WorkflowState(TypedDict):
    ticket_id: str
    ticket_description: str
    ticket_type: str
    
    # Localization (Phase 2)
    localization_result: LocalizationResult
    candidate_files: list[FileCandidate]
    method_targets: list[MethodTarget]
    blast_radius: ImpactReport
    human_approved_files: bool  # Early checkpoint
    
    # Context
    expanded_context: dict[str, str]
    
    # Patching (Phase 3)
    patches: list[Patch]
    applied_files: list[str]
    
    # Validation (Phase 4)
    static_validation: StaticValidationResult
    runtime_validation: RuntimeValidationResult
    quality_gates: QualityGateResult
    
    # Control
    current_node: str
    retry_count: int
    human_approved: bool
    errors: list[str]
```

---

## 🎨 Phase 6: Transparent Engineering UI

**Goal:** Human-observable, controllable engineering workspace.

**IMPORTANT:** This is NOT a "chatbot interface". This is a **Repository Intelligence Workspace**.

Chat is auxiliary. Primary focus:
- Graph visualization
- Patch viewer
- Retrieval viewer
- Execution traces
- Runtime screenshots
- File explorer
- Dependency explorer

### Frontend Architecture (Next.js)

```
┌────────────────────────────────────────────────────────────┐
│                      App Header                            │
│  Aviator │ Project: My App │ Index: 1,247 symbols          │
└────────────────────────────────────────────────────────────┘
┌──────────────────┬─────────────────────────────────────────┐
│  Left Sidebar    │         Main Engineering Panel          │
│  (300px)         │                                         │
│                  │  ┌───────────────────────────────────┐ │
│  📂 Repository   │  │  Phase: Localization (CRITICAL)   │ │
│    - Explorer    │  │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │ │
│    - Symbols     │  │  Progress: Awaiting human review  │ │
│    - Search      │  └───────────────────────────────────┘ │
│                  │                                         │
│  🔍 Localization │  ┌───────────────────────────────────┐ │
│    Results       │  │  Localized Files (Score)          │ │
│                  │  │  ─────────────────────────────────│ │
│  🎯 Current Task │  │  ✓ UserService.java      (0.92)  │ │
│    Ticket-123    │  │    Method: saveUser(User)        │ │
│                  │  │    Reason: Contains registration  │ │
│  📊 Impact       │  │                                    │ │
│    Direct: 3     │  │  ✓ UserRepository.java   (0.85)  │ │
│    Transitive:12 │  │    Method: save(User)             │ │
│    Tests: 8      │  │    Reason: Called by saveUser    │ │
│                  │  │                                    │ │
│                  │  │  ✗ PaymentService.java   (0.45)  │ │
│                  │  │    [Low confidence - exclude]     │ │
│                  │  │                                    │ │
│                  │  │  [✓ Approve] [+ Add] [- Remove]   │ │
│                  │  └───────────────────────────────────┘ │
│                  │                                         │
│                  │  ┌───────────────────────────────────┐ │
│                  │  │  Dependency Graph                 │ │
│                  │  │  ─────────────────────────────────│ │
│                  │  │    UserController                 │ │
│                  │  │         ↓                          │ │
│                  │  │    UserService ← (YOU ARE HERE)   │ │
│                  │  │         ↓                          │ │
│                  │  │    UserRepository                 │ │
│                  │  └───────────────────────────────────┘ │
│                  │                                         │
│                  │  ┌───────────────────────────────────┐ │
│                  │  │  Generated Patches                │ │
│                  │  │  ─────────────────────────────────│ │
│                  │  │  📄 UserService.java              │ │
│                  │  │     Method: saveUser()            │ │
│                  │  │     Lines: 45-67                  │ │
│                  │  │     [View Diff] [Edit] [Reject]   │ │
│                  │  └───────────────────────────────────┘ │
│                  │                                         │
│                  │  ┌───────────────────────────────────┐ │
│                  │  │  Validation Results               │ │
│                  │  │  ─────────────────────────────────│ │
│                  │  │  ✓ Build: SUCCESS                 │ │
│                  │  │  ✓ Unit Tests: 24/24 passed       │ │
│                  │  │  ✓ Playwright: Login flow OK      │ │
│                  │  │  ⚠ Code Quality: 2 warnings       │ │
│                  │  └───────────────────────────────────┘ │
│                  │                                         │
│                  │  [← Previous] [Approve & Continue →]   │
└──────────────────┴─────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────┐
│  Right Panel: Execution Trace / Chat (auxiliary)           │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  🔎 Execution Trace                                  │ │
│  │  ────────────────────────────────────────────────────│ │
│  │  Localization Strategy:                              │ │
│  │  1. Keyword: "registration" → 12 files              │ │
│  │  2. Symbol: "User" → 8 classes                      │ │
│  │  3. Graph: UserController → UserService found       │ │
│  │  4. AST: @Transactional methods → 3 candidates      │ │
│  │  5. Ranking: Top 2 files above 0.7 threshold        │ │
│  │                                                       │ │
│  │  💬 Chat (auxiliary)                                 │ │
│  │  ────────────────────────────────────────────────────│ │
│  │  User: Why did you pick UserService?                │ │
│  │  System: Graph analysis shows UserController calls  │ │
│  │  UserService.saveUser(). Registration logic lives   │ │
│  │  in that method (0.92 confidence from AST match).   │ │
│  │                                                       │ │
│  │  📋 Logs                                             │ │
│  │  ────────────────────────────────────────────────────│ │
│  │  [12:34:56] Workflow started                        │ │
│  │  [12:34:57] Issue classified as: bug                │ │
│  │  [12:34:58] Hybrid retrieval: 47 candidates         │ │
│  │  [12:34:59] Ranked top 4 files                      │ │
│  │  [12:35:02] Dependency expansion complete           │ │
│  │  [12:35:05] Patch generated for UserService.java   │ │
│  └──────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

### React Components

#### 1. Workflow Stepper
```tsx
// components/WorkflowStepper.tsx
const steps = [
  "Classify",
  "Localize",
  "Expand",
  "Plan",
  "Generate",
  "Validate",
  "Review"
];

export function WorkflowStepper({ currentStep }: Props) {
  return (
    <div className="stepper">
      {steps.map((step, i) => (
        <div key={i} className={i === currentStep ? "active" : ""}>
          {step}
        </div>
      ))}
    </div>
  );
}
```

#### 2. File Candidate List
```tsx
// components/FileCandidateList.tsx
export function FileCandidateList({ files, onToggle }: Props) {
  return (
    <div className="file-list">
      {files.map(file => (
        <div key={file.path} className="file-item">
          <input
            type="checkbox"
            checked={file.selected}
            onChange={() => onToggle(file.path)}
          />
          <span>{file.path}</span>
          <span className="confidence">{file.confidence.toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}
```

#### 3. Dependency Graph Visualization
```tsx
// components/DependencyGraph.tsx
import ReactFlow from 'reactflow';

export function DependencyGraph({ nodes, edges }: Props) {
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      fitView
    />
  );
}
```

#### 4. Diff Viewer
```tsx
// components/DiffViewer.tsx
import { Diff } from 'react-diff-viewer';

export function DiffViewer({ patch }: Props) {
  return (
    <Diff
      oldValue={patch.old_text}
      newValue={patch.new_text}
      splitView={true}
      showDiffOnly={false}
    />
  );
}
```

#### 5. Playwright Screenshot Viewer
```tsx
// components/PlaywrightResults.tsx
export function PlaywrightResults({ results }: Props) {
  return (
    <div className="playwright-results">
      {results.map(result => (
        <div key={result.id}>
          <h4>{result.workflow_name}</h4>
          {result.success ? (
            <span className="success">✓ Passed</span>
          ) : (
            <span className="failure">✗ Failed: {result.error}</span>
          )}
          <img src={result.screenshot} alt="Screenshot" />
          <video src={result.video} controls />
        </div>
      ))}
    </div>
  );
}
```

### WebSocket Integration

```tsx
// hooks/useWorkflowStream.ts
export function useWorkflowStream(taskId: string) {
  const [state, setState] = useState<WorkflowState | null>(null);
  
  useEffect(() => {
    const ws = new WebSocket(`ws://localhost:8000/ws/workflow/${taskId}`);
    
    ws.onmessage = (event) => {
      const update = JSON.parse(event.data);
      setState(prev => ({
        ...prev,
        currentNode: update.node,
        data: { ...prev?.data, ...update.data }
      }));
    };
    
    return () => ws.close();
  }, [taskId]);
  
  return state;
}
```

---

## 🚀 Phase 7: Production Features

### 1. Incremental Indexing (VERY IMPORTANT for scale)

```python
class IncrementalIndexer:
    def index_changes(self, repo_path: Path, since: str = "HEAD~1"):
        """Index only changed files since git commit."""
        
        # Get changed files from git diff
        result = subprocess.run(
            ["git", "diff", "--name-only", since],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        
        changed_files = [
            Path(f) for f in result.stdout.split()
            if f.endswith(".java")
        ]
        
        # Re-index only those files
        parser = JavaParser()
        store = SqliteStore(repo_path / ".aviator" / "index.db")
        
        with store.transaction():
            for file_path in changed_files:
                if not file_path.exists():
                    # File was deleted
                    store.delete_file(file_path)
                else:
                    # File was modified
                    result = parser.parse_file(file_path, repo_path)
                    store.upsert_file(result.file)
                    store.insert_symbols(result.symbols)
                    store.insert_edges(result.edges)
        
        # CRITICAL: Partial graph update
        self.update_graph_incrementally(changed_files)
    
    def update_graph_incrementally(self, changed_files: list[Path]):
        """Update Neo4j graph for only changed files."""
        
        # Delete old nodes/edges for changed files
        for file_path in changed_files:
            graph.query("""
                MATCH (f:File {path: $path})-[r]->()
                DELETE r
            """, path=str(file_path))
            
            graph.query("""
                MATCH (f:File {path: $path})
                DELETE f
            """, path=str(file_path))
        
        # Re-insert symbols and edges from SQLite
        # (this avoids full graph rebuild)
        for file_path in changed_files:
            symbols = store.get_symbols_for_file(file_path)
            edges = store.get_edges_for_file(file_path)
            
            for symbol in symbols:
                graph.create_node(symbol)
            
            for edge in edges:
                graph.create_edge(edge)
```

### 2. Git Integration

```python
class GitIntegration:
    def create_feature_branch(self, ticket_id: str) -> str:
        """Create branch for ticket."""
        branch_name = f"feature/ticket-{ticket_id}"
        subprocess.run(["git", "checkout", "-b", branch_name])
        return branch_name
    
    def commit_changes(self, message: str, files: list[str]):
        """Commit modified files."""
        subprocess.run(["git", "add"] + files)
        subprocess.run(["git", "commit", "-m", message])
    
    def create_pull_request(
        self,
        title: str,
        description: str,
        branch: str
    ) -> str:
        """Create GitHub PR."""
        # Use GitHub API
        response = requests.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers={"Authorization": f"token {github_token}"},
            json={
                "title": title,
                "body": description,
                "head": branch,
                "base": "main"
            }
        )
        return response.json()["html_url"]
```

### 3. Rollback System

```python
class RollbackManager:
    def create_checkpoint(self, files: list[str]) -> str:
        """Create backup before applying patches."""
        checkpoint_id = str(uuid.uuid4())
        checkpoint_dir = Path(f".aviator/checkpoints/{checkpoint_id}")
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        
        for file_path in files:
            shutil.copy(
                file_path,
                checkpoint_dir / Path(file_path).name
            )
        
        return checkpoint_id
    
    def rollback(self, checkpoint_id: str):
        """Restore files from checkpoint."""
        checkpoint_dir = Path(f".aviator/checkpoints/{checkpoint_id}")
        
        for backup_file in checkpoint_dir.iterdir():
            original_path = find_original_path(backup_file.name)
            shutil.copy(backup_file, original_path)
```

### 4. Observability & Telemetry

```python
# Prometheus metrics
from prometheus_client import Counter, Histogram, Gauge

workflow_started = Counter(
    "workflow_started_total",
    "Total workflows started",
    ["ticket_type"]
)

workflow_duration = Histogram(
    "workflow_duration_seconds",
    "Workflow execution time",
    ["phase"]
)

retrieval_quality = Gauge(
    "retrieval_confidence_score",
    "Average confidence of localization",
    ["strategy"]
)

patch_success_rate = Counter(
    "patch_application_total",
    "Patch application results",
    ["result"]  # success, syntax_error, merge_conflict
)
```

---

## � Execution Flow Intelligence

**Goal:** Understand runtime execution paths (Controller → Service → Repository).

This is VERY important for enterprise debugging.

### Execution Path Analysis

```python
class ExecutionFlowAnalyzer:
    def analyze_path(
        self,
        entry_point: str,
        target_symbol: str
    ) -> ExecutionPath:
        """Trace execution from entry to target."""
        
        # Find all paths from controller to target
        paths = graph.query("""
            MATCH path = (entry:Symbol {qualified_name: $entry})
                        -[:CALLS*1..10]->
                        (target:Symbol {qualified_name: $target})
            WHERE entry.annotations CONTAINS 'RestController'
               OR entry.annotations CONTAINS 'Controller'
            RETURN path
            ORDER BY length(path)
            LIMIT 5
        """, entry=entry_point, target=target_symbol)
        
        return ExecutionPath(
            paths=paths,
            shortest_path=paths[0] if paths else None,
            layers_traversed=analyze_layers(paths[0])
        )
    
    def find_execution_neighborhood(
        self,
        symbol_id: str,
        radius: int = 2
    ) -> ExecutionNeighborhood:
        """Find all symbols within execution radius."""
        
        # Bidirectional traversal
        callers = graph.query("""
            MATCH (caller)-[:CALLS*1..$radius]->(s:Symbol {id: $id})
            RETURN DISTINCT caller
        """, id=symbol_id, radius=radius)
        
        callees = graph.query("""
            MATCH (s:Symbol {id: $id})-[:CALLS*1..$radius]->(callee)
            RETURN DISTINCT callee
        """, id=symbol_id, radius=radius)
        
        return ExecutionNeighborhood(
            upstream=callers,
            downstream=callees,
            layers=detect_architecture_layers(callers + callees)
        )
```

### Layer Detection

```python
def detect_architecture_layer(symbol: Symbol) -> str:
    """Detect which architecture layer a symbol belongs to."""
    
    # Controller layer
    if any(ann in symbol.annotations for ann in 
           ['RestController', 'Controller', 'RequestMapping']):
        return 'controller'
    
    # Service layer
    if any(ann in symbol.annotations for ann in 
           ['Service', 'Component', 'Transactional']):
        return 'service'
    
    # Repository layer
    if any(ann in symbol.annotations for ann in 
           ['Repository', 'Entity', 'Table']):
        return 'repository'
    
    # Utility
    if 'Util' in symbol.name or 'Helper' in symbol.name:
        return 'utility'
    
    return 'unknown'
```

---

## 💥 Blast-Radius Analysis

**Goal:** Understand impact BEFORE patching.

VERY important for enterprise safety.

### Impact Report

```python
class BlastRadiusAnalyzer:
    def analyze(
        self,
        target_symbols: list[str]
    ) -> ImpactReport:
        """Comprehensive impact analysis."""
        
        report = ImpactReport()
        
        for symbol_id in target_symbols:
            # Direct callers (1 hop)
            direct_callers = self.find_callers(symbol_id, hops=1)
            report.direct_callers.extend(direct_callers)
            
            # Transitive dependencies (3 hops)
            transitive = self.find_callers(symbol_id, hops=3)
            report.transitive_dependents.extend(transitive)
            
            # Affected tests
            tests = self.find_tests_covering(symbol_id)
            report.affected_tests.extend(tests)
            
            # API surface
            if self.is_public_api(symbol_id):
                report.breaks_public_api = True
                report.api_endpoints.append(
                    self.get_api_endpoint(symbol_id)
                )
            
            # Cross-service impact
            if self.is_service_boundary(symbol_id):
                report.cross_service_impact = True
                report.affected_services.extend(
                    self.find_dependent_services(symbol_id)
                )
        
        # Risk level
        report.risk_level = self.calculate_risk(report)
        
        return report
    
    def calculate_risk(self, report: ImpactReport) -> str:
        """Calculate overall risk level."""
        
        if report.breaks_public_api:
            return "CRITICAL"
        elif report.cross_service_impact:
            return "HIGH"
        elif len(report.transitive_dependents) > 20:
            return "HIGH"
        elif len(report.direct_callers) > 5:
            return "MEDIUM"
        else:
            return "LOW"
```

### Impact Visualization

```python
def visualize_blast_radius(
    target: str,
    impact: ImpactReport
) -> BlastRadiusGraph:
    """Generate graph showing impact radius."""
    
    nodes = [
        {"id": target, "label": target, "color": "red", "size": 10}
    ]
    
    edges = []
    
    # Direct callers (orange)
    for caller in impact.direct_callers:
        nodes.append({
            "id": caller.id,
            "label": caller.name,
            "color": "orange",
            "size": 8
        })
        edges.append({"from": caller.id, "to": target, "color": "orange"})
    
    # Transitive (yellow)
    for dep in impact.transitive_dependents:
        nodes.append({
            "id": dep.id,
            "label": dep.name,
            "color": "yellow",
            "size": 6
        })
        edges.append({"from": dep.id, "to": target, "color": "yellow"})
    
    return BlastRadiusGraph(nodes=nodes, edges=edges)
```

---

## �🛠️ Technology Stack

### Frontend
- **Framework:** Next.js 14+ (App Router) OR React 18+ with Vite
- **State Management:** Zustand / Redux Toolkit
- **WebSocket:** Socket.IO client
- **UI Components:** 
  - shadcn/ui (Radix + Tailwind)
  - react-flow (graph visualization)
  - react-diff-viewer (patch diffs)
- **Styling:** Tailwind CSS

### Backend
- **API Framework:** FastAPI 0.115+
- **Workflow Engine:** LangGraph
- **LLM:** ADT Aviator model (via API)
- **Task Queue:** Celery (optional, for long-running jobs)
- **WebSocket:** FastAPI WebSocket support

### Repository Intelligence
- **Language:** Python 3.11+
- **AST Parsing:** tree-sitter-java (tree-sitter-languages for others)
- **Data Models:** Pydantic 2.7+
- **CLI:** Typer + Rich

### Databases

#### SQLite (Primary Index)
- **Purpose:** Symbol index, file metadata, edges, FTS5 search
- **Location:** `<repo>/.aviator/index.db`
- **Schema:** files, symbols, edges, symbols_fts

#### Neo4j (Graph DB, Optional)
```cypher
// Example schema
(:File)-[:CONTAINS]->(:Class)
(:Class)-[:EXTENDS]->(:Class)
(:Class)-[:IMPLEMENTS]->(:Interface)
(:Method)-[:CALLS]->(:Method)
(:Class)-[:DEPENDS_ON]->(:Class)
```

#### Qdrant (Vector DB, Optional)
```python
# Collection schema
collection_name = "code_symbols"
vectors = {
    "id": symbol.id,
    "vector": embedding(symbol.qualified_name + symbol.doc),
    "payload": {
        "qualified_name": symbol.qualified_name,
        "kind": symbol.kind,
        "path": symbol.location.path
    }
}
```

### Testing & Validation
- **Unit Tests:** pytest
- **Build:** Maven (Java), npm (TypeScript)
- **UI Testing:** Playwright
- **Code Quality:** 
  - SonarQube (Java, TypeScript, Python)
  - ESLint (JavaScript/TypeScript)
  - Checkstyle (Java)
  - Black/Ruff (Python)

### Infrastructure
- **Containerization:** Docker + Docker Compose
- **CI/CD:** GitHub Actions
- **Monitoring:** Prometheus + Grafana
- **Logging:** Structured JSON logs (stdout)

---

## 🎭 Agent System (MINIMIZED)

**IMPORTANT:** Keep agent count LOW. Prefer deterministic components.

### Minimal Agent Architecture

**WRONG APPROACH: Too many agents**
```
❌ PlanningAgent
❌ RetrievalAgent  
❌ ValidationAgent
❌ AnalysisAgent
❌ RefactoringAgent
❌ TestGenerationAgent
❌ DocumentationAgent
❌ ReviewAgent
... (causes orchestration failures)
```

**RIGHT APPROACH: Fewer, stronger components**

```
┌─────────────────────────────────────────────────────────┐
│           Component Architecture (NOT Agent Swarm)       │
│                                                          │
│  ┌────────────────────────────────────────────────────┐│
│  │  LLM Reasoning (ADT Aviator)                       ││
│  │  - Ticket classification                           ││
│  │  - Patch generation (LLM-based)                    ││
│  │  - Failure analysis                                ││
│  └────────────────────────────────────────────────────┘│
│                                                          │
│  ┌────────────────────────────────────────────────────┐│
│  │  Hybrid Localizer (DETERMINISTIC)                  ││
│  │  - AST + Graph + FTS primary                       ││
│  │  - Vectors secondary                               ││
│  │  - 9-step pipeline                                 ││
│  └────────────────────────────────────────────────────┘│
│                                                          │
│  ┌────────────────────────────────────────────────────┐│
│  │  Patch Applicator (DETERMINISTIC)                  ││
│  │  - Tree-sitter range replacement                   ││
│  │  - Syntax validation                               ││
│  └────────────────────────────────────────────────────┘│
│                                                          │
│  ┌────────────────────────────────────────────────────┐│
│  │  Validation Pipeline (DETERMINISTIC)               ││
│  │  - Build, test, lint (static)                     ││
│  │  - Integration, Playwright (runtime)              ││
│  └────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

**Philosophy:** Architecture intelligence > agent count.

### Agent Communication Protocol

```python
class AgentMessage(BaseModel):
    agent: str  # source agent
    target: str  # destination agent
    type: str  # request, response, event
    data: dict
    timestamp: datetime

class AgentOrchestrator:
    agents: dict[str, Agent]
    message_bus: MessageBus
    
    async def dispatch(self, message: AgentMessage):
        """Route messages between agents."""
        target_agent = self.agents[message.target]
        response = await target_agent.handle(message)
        return response
```

### RAG Agent (Knowledge Base)

```python
class RAGAgent:
    """Retrieves domain knowledge from uploaded documentation."""
    
    def __init__(self, vector_store: Qdrant, kb_path: Path):
        self.vector_store = vector_store
        self.kb_path = kb_path
    
    async def retrieve_context(
        self,
        query: str,
        top_k: int = 5
    ) -> list[Document]:
        """Retrieve relevant docs from knowledge base."""
        
        # Embed query
        query_vector = self.embed(query)
        
        # Search Qdrant
        results = self.vector_store.search(
            collection_name="knowledge_base",
            query_vector=query_vector,
            limit=top_k
        )
        
        return [
            Document(
                content=r.payload["content"],
                metadata=r.payload["metadata"],
                score=r.score
            )
            for r in results
        ]
    
    def index_knowledge_base(self, docs: list[Path]):
        """Index uploaded documentation."""
        for doc_path in docs:
            content = read_markdown(doc_path)
            chunks = split_into_chunks(content, chunk_size=512)
            
            for chunk in chunks:
                vector = self.embed(chunk.text)
                self.vector_store.upsert(
                    collection_name="knowledge_base",
                    points=[{
                        "id": chunk.id,
                        "vector": vector,
                        "payload": {
                            "content": chunk.text,
                            "metadata": {
                                "source": str(doc_path),
                                "section": chunk.section
                            }
                        }
                    }]
                )
```

---

## 💾 Database Layer

### SQLite Schema (Primary)

```sql
-- Repository index database: <repo>/.aviator/index.db

CREATE TABLE files (
    path           TEXT PRIMARY KEY,
    language       TEXT NOT NULL,
    package        TEXT,
    sha256         TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    parse_ok       INTEGER NOT NULL,
    parse_error    TEXT,
    indexed_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE symbols (
    id             TEXT PRIMARY KEY,      -- stable hash
    kind           TEXT NOT NULL,         -- class, method, field, etc.
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    package        TEXT,
    parent_id      TEXT,                  -- FK to symbols.id
    path           TEXT NOT NULL,         -- FK to files.path
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    start_col      INTEGER NOT NULL,
    end_col        INTEGER NOT NULL,
    signature      TEXT,                  -- for methods
    return_type    TEXT,
    modifiers      TEXT,                  -- JSON array
    annotations    TEXT,                  -- JSON array
    parameter_types TEXT,                 -- JSON array
    FOREIGN KEY (path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX idx_symbols_name ON symbols(name);
CREATE INDEX idx_symbols_qualified_name ON symbols(qualified_name);
CREATE INDEX idx_symbols_kind ON symbols(kind);
CREATE INDEX idx_symbols_path ON symbols(path);

CREATE TABLE edges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,         -- contains, extends, implements, calls
    src_id         TEXT NOT NULL,         -- FK to symbols.id
    dst_id         TEXT,                  -- FK to symbols.id (nullable)
    dst_name       TEXT NOT NULL,         -- textual target
    path           TEXT,
    start_line     INTEGER,
    FOREIGN KEY (src_id) REFERENCES symbols(id) ON DELETE CASCADE
);

CREATE INDEX idx_edges_src ON edges(src_id);
CREATE INDEX idx_edges_dst ON edges(dst_id);
CREATE INDEX idx_edges_dst_name ON edges(dst_name);

-- FTS5 for fast text search
CREATE VIRTUAL TABLE symbols_fts USING fts5(
    name,
    qualified_name,
    signature,
    content='symbols',
    content_rowid='rowid'
);
```

### Neo4j Schema (Optional Graph DB)

```cypher
// Nodes
(:File {path, language, package, sha256})
(:Symbol {id, kind, name, qualified_name, signature})

// Relationships
(:File)-[:CONTAINS]->(:Symbol)
(:Symbol)-[:CONTAINS]->(:Symbol)        // nested classes, methods in class
(:Symbol)-[:EXTENDS]->(:Symbol)         // inheritance
(:Symbol)-[:IMPLEMENTS]->(:Symbol)      // interface implementation
(:Symbol)-[:CALLS]->(:Symbol)           // method calls
(:Symbol)-[:REFERENCES]->(:Symbol)      // field/type references
(:Symbol)-[:ANNOTATED_BY {annotation}]->()
(:Symbol)-[:HAS_TYPE {type}]->()

// Example queries

// Find all callers of a method
MATCH (caller:Symbol)-[:CALLS]->(method:Symbol {name: 'saveUser'})
RETURN caller.qualified_name

// Find execution path from controller to repository
MATCH path = (controller:Symbol {kind: 'class'})-[:CALLS*]->(repo:Symbol {kind: 'class'})
WHERE controller.annotations CONTAINS 'RestController'
  AND repo.annotations CONTAINS 'Repository'
RETURN path

// Find blast radius of a change
MATCH (changed:Symbol {id: 'abc123'})<-[:CALLS*1..3]-(affected)
RETURN DISTINCT affected.qualified_name
```

### Qdrant Schema (Vector DB)

```python
# Collection: code_symbols
{
    "name": "code_symbols",
    "vectors": {
        "size": 384,  # all-MiniLM-L6-v2
        "distance": "Cosine"
    }
}

# Points
{
    "id": "symbol_abc123",
    "vector": [0.123, -0.456, ...],  # 384-dim embedding
    "payload": {
        "symbol_id": "abc123",
        "qualified_name": "com.example.UserService#saveUser(User)",
        "kind": "method",
        "path": "src/main/java/com/example/UserService.java",
        "signature": "saveUser(User)",
        "doc": "Saves a user to the database..."
    }
}

# Collection: knowledge_base
{
    "name": "knowledge_base",
    "vectors": {
        "size": 384,
        "distance": "Cosine"
    }
}

# Points
{
    "id": "kb_chunk_001",
    "vector": [0.789, 0.234, ...],
    "payload": {
        "content": "## Transaction Management\n\nSpring uses...",
        "source": "spring-docs.md",
        "section": "Transaction Management"
    }
}
```

---

## 🏭 Infrastructure

### Docker Compose Setup

```yaml
# aviator-plugin-sample/docker-compose.yml
services:
  # Backend API
  backend:
    build: ./chatbot/backend
    ports:
      - "8000:8000"
    volumes:
      - ./chatbot/backend:/app
      - /var/run/docker.sock:/var/run/docker.sock  # for Docker-in-Docker
    environment:
      - DATABASE_URL=sqlite:///data/aviator.db
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=aviator-dev
      - QDRANT_HOST=qdrant
      - QDRANT_PORT=6333
      - ADT_AVIATOR_API_KEY=${ADT_AVIATOR_API_KEY}
    depends_on:
      - neo4j
      - qdrant

  # Frontend UI
  frontend:
    build: ./chatbot/frontend
    ports:
      - "3000:3000"
    volumes:
      - ./chatbot/frontend:/app
      - /app/node_modules
    environment:
      - VITE_API_URL=http://localhost:8000

  # Neo4j graph database
  neo4j:
    image: neo4j:5.20-community
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      - NEO4J_AUTH=neo4j/aviator-dev
      - NEO4J_PLUGINS=["apoc"]
    volumes:
      - neo4j-data:/data

  # Qdrant vector database
  qdrant:
    image: qdrant/qdrant:v1.9.7
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant-data:/qdrant/storage

volumes:
  neo4j-data:
  qdrant-data:
```

### Deployment Architecture (Production)

```
┌─────────────────────────────────────────────────────────┐
│                    Load Balancer                         │
│                    (nginx / AWS ALB)                     │
└─────────────────┬───────────────────────────────────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
┌───────▼──────┐   ┌───────▼──────┐
│  Frontend    │   │  Frontend    │
│  (Next.js)   │   │  (Next.js)   │
│  Container   │   │  Container   │
└──────┬───────┘   └──────┬───────┘
       │                  │
       └────────┬─────────┘
                │
┌───────────────▼────────────────────────────────────────┐
│                    API Gateway                          │
└───────────────┬────────────────────────────────────────┘
                │
        ┌───────┴───────┐
        │               │
┌───────▼──────┐ ┌─────▼────────┐
│  Backend     │ │  Backend     │
│  (FastAPI)   │ │  (FastAPI)   │
│  Container   │ │  Container   │
└──────┬───────┘ └──────┬───────┘
       │                │
       └────────┬───────┘
                │
┌───────────────▼────────────────────────────────────────┐
│                Database Layer                           │
│  ┌──────────┬──────────┬──────────┬──────────────┐    │
│  │ Neo4j    │ Qdrant   │ Redis    │ S3 (backups) │    │
│  │ Cluster  │ Cluster  │ (cache)  │              │    │
│  └──────────┴──────────┴──────────┴──────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## 📊 Implementation Phases Summary

| Phase | Status | Components | Duration |
|-------|--------|------------|----------|
| **Phase 1** | ✅ Complete | AST parser, SQLite store, CLI | 1 week |
| **Phase 2** | 🔜 Next | **Hybrid Localization (MOST CRITICAL)** | 3 weeks |
| **Phase 3** | 📋 Planned | Patch generation, tree-sitter ranges | 2 weeks |
| **Phase 4** | 📋 Planned | Validation pipeline, Playwright | 2 weeks |
| **Phase 5** | 📋 Planned | LangGraph orchestration (lightweight) | 2 weeks |
| **Phase 6** | 📋 Planned | Engineering workspace UI | 3 weeks |
| **Phase 7** | 📋 Planned | Incremental indexing, Git, telemetry | 2 weeks |

**Total estimated time:** 15 weeks (3.75 months)

**Phase 2 is longest because localization quality determines system success.**

---

## 🎯 Current Project Status

### ✅ Completed

1. **Repository Intelligence Core** (`aviator-platform/`)
   - tree-sitter-java parser extracting classes, methods, fields, calls, inheritance
   - SQLite storage with FTS5 search
   - CLI: `aviator index`, `search`, `show`, `callers`
   - 3 passing smoke tests

2. **Basic Chatbot UI** (`aviator-plugin-sample/chatbot/`)
   - FastAPI backend (port 8000)
   - React frontend (port 3002)
   - 2-panel layout: Projects + Chatbot
   - WebSocket support

3. **Project Infrastructure**
   - Docker Compose for Neo4j + Qdrant
   - Python 3.13 venv
   - Clean folder structure

### 🔜 Next Steps (Immediate)

**Recommended: Start Phase 2 (Hybrid Localization Engine)**

This is THE most critical phase. Priority:

1. Build `HybridLocalizer` class (AST + Graph primary, vectors secondary)
2. Implement 9-step localization pipeline
3. Add blast-radius analyzer
4. Add human review checkpoint
5. Test on real tickets from your Java repo
6. Measure localization accuracy (precision/recall)

**Alternative: Wire indexer into UI first**
- Add `/api/index` endpoint
- Show indexing progress via WebSocket
- Then proceed to Phase 2

---

## 📝 Notes

### Design Decisions

1. **Why SQLite first?** Simplicity. Zero ops, single file, FTS5 built-in. Neo4j/Qdrant optional for scale.

2. **Why tree-sitter over JavaParser (Java lib)?** No JVM dependency. Python-native. Works for Java, C#, TypeScript, Python with same API.

3. **Why AST + Graph over vectors?** Deterministic. Vectors cause hallucinated localization. AST + graph follow actual code structure.

4. **Why LangGraph late (Phase 5)?** Orchestration only makes sense AFTER you have strong deterministic components. LangGraph can't fix weak localization.

5. **Why patch-first?** Large codebases have hundreds of files. Rewriting entire files wastes tokens, breaks formatting, and risks merge conflicts. Patches target specific methods.

6. **Why ADT Aviator only?** User constraint. No OpenAI/Anthropic direct calls.

7. **Why human review after localization?** Wrong files destroy entire pipeline. Early validation checkpoint prevents wasted work.

8. **Why is this NOT a chatbot?** Chat is auxiliary. This is an engineering workspace for repository intelligence, not a conversational interface.

### Key Risks

1. **Localization accuracy** — If retrieval fails, everything fails. This is the highest risk. Mitigate with hybrid strategies + human validation.

2. **Over-reliance on vectors** — Semantic search alone causes hallucinations. Mitigate by making AST + graph primary.

3. **Call resolution accuracy** — Name-based call edges are best-effort. Type-aware resolution requires full symbol resolution (expensive).

4. **Patch merge conflicts** — If user edits file while workflow runs, patch may fail. Mitigate with checksums + retry.

5. **LLM hallucination** — Patch generation may produce incorrect logic. Mitigate with validation gates + human review.

6. **Scale** — Large repos (100k+ files) may overwhelm FTS5. Migrate to Neo4j + Elasticsearch.

7. **Agent complexity** — Too many agents cause orchestration failures. Keep agent count minimal.

### Future Enhancements

- Multi-language support (C#, TypeScript, Python, Go)
- Incremental indexing via Git hooks
- Semantic code search via Qdrant
- Change impact analysis
- Automated refactoring workflows
- Integration with Jira, Linear, GitHub Issues

---

## ✅ FINAL ARCHITECTURAL VERDICT

Your architecture is now correctly ordered and properly prioritized:

### Core Philosophy ✅
- Repository Intelligence Platform + Patch Safety System + Validation Infrastructure
- LLM is ONLY the reasoning layer
- AST + Graph are PRIMARY, vectors are SECONDARY

### Phase Priorities ✅
1. **Repository Intelligence Core** (complete)
2. **Hybrid Localization Engine** (MOST CRITICAL — 3 weeks)
3. **Patch Generation Engine** (2 weeks)
4. **Validation Infrastructure** (2 weeks)
5. **LangGraph Orchestration** (lightweight, 2 weeks)
6. **Engineering Workspace UI** (not chatbot, 3 weeks)
7. **Production Features** (2 weeks)

### Key Corrections Applied ✅
- ✅ Localization moved to Phase 2 (was Phase 3)
- ✅ LangGraph moved to Phase 5 (was Phase 3)
- ✅ "Code Generation" replaced with "AST-safe Patch Generation"
- ✅ Added Blast-Radius Analysis section
- ✅ Added Execution Flow Intelligence section
- ✅ Added incremental graph update strategy
- ✅ Human review checkpoint added AFTER localization
- ✅ Separated Static vs Runtime validation
- ✅ UI renamed from "chatbot" to "Repository Intelligence Workspace"
- ✅ Agent system minimized (deterministic components > agent swarm)
- ✅ Vector DB correctly de-emphasized (fallback only)

### Success Metric ✅
**"Correct localization is MORE important than generation quality"** is now the guiding principle.

---

**Last Updated:** Phase 1 Complete — Architecture Restructured — May 21, 2026
