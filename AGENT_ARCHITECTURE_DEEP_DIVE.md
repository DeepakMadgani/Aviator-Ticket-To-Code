# Aviator Ticket-to-Code — Agent Architecture Deep Dive

> Every agent, every prompt, every data source, every decision — explained in full.

---

## Table of Contents

1. [System Overview & Flow](#1-system-overview--flow)
2. [Agent 0 — Investigation Agent](#2-agent-0--investigation-agent)
3. [Agent 1 — Ticket Analyzer (Unified Analysis)](#3-agent-1--ticket-analyzer-unified-analysis)
4. [Agent 2 — Planning Agent](#4-agent-2--planning-agent)
5. [Agent 2.5 — Localization Agent](#5-agent-25--localization-agent)
6. [Agent 3A/3B — RAG Engine (Context Retrieval)](#6-agent-3a3b--rag-engine-context-retrieval)
7. [Agent 4A — Test Generator](#7-agent-4a--test-generator)
8. [Agent 4B — Code Generator](#8-agent-4b--code-generator)
9. [Patch Validator (Scope Guard)](#9-patch-validator-scope-guard)
10. [Agent 5 — Build Executor](#10-agent-5--build-executor)
11. [Agent 6 — Test Executor](#11-agent-6--test-executor)
12. [Data Sources Reference](#12-data-sources-reference)
13. [Why Each Constraint Exists](#13-why-each-constraint-exists)

---

## 1. System Overview & Flow

```
User submits ticket (title + description + project)
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   LangGraph Workflow                             │
│                                                                 │
│  [0] investigate_node                                           │
│       │                                                         │
│  [1] unified_analysis_node                                      │
│       │                    └── non-code path → solution_guidance│
│  [2] plan_node                                                  │
│       │                                                         │
│  [2.5] localize_node  ← THE MOST CRITICAL STEP                 │
│       │                                                         │
│  [3A] rag_for_tests ──────┐                                    │
│  [3B] rag_for_code  ──────┤ (parallel)                         │
│                           ▼                                     │
│  [4A] generate_tests ─────┐                                    │
│  [4B] generate_code  ─────┤ (parallel)                         │
│                    PatchValidator runs here                     │
│                           ▼                                     │
│  [5] build_node                                                 │
│       │                                                         │
│  [6] test_node                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**State object**: `TicketToCodeState` (TypedDict) carries all data between nodes.  
**LLM**: `gemini-2.0-flash` via `ChatGoogleGenerativeAI` (Vertex AI, `europe-west4`).  
**Entry file**: `aviator-plugin-sample/src/ticket_to_code/workflow.py`

---

## 2. Agent 0 — Investigation Agent

**File**: `src/ticket_to_code/agents/investigation_agent.py`  
**Node**: `investigate_node()` in `workflow.py`

### What it does
First-pass triage. Classifies the ticket and decides whether code changes are even needed. If the ticket is a "how do I configure X" question, it routes to solution guidance instead of code generation — avoiding wasted LLM calls.

### Input
| Source | Data |
|--------|------|
| `state["ticket"]` | `ValueEdgeTicket` — id, title, description, priority |
| `codebase_summary` | Simple string: `"Codebase at: {workspace_path}"` |

### Output
`InvestigationResult` written to `state["investigation_result"]`:
```python
{
  "ticket_type": "feature" | "bug" | "config" | "investigation" | ...,
  "requires_code_changes": True/False,
  "confidence": 0.0–1.0,
  "root_cause_hypothesis": "string",
  "affected_systems": ["xchange-ui", "area-service", ...],
  "investigation_areas": ["footer", "copyright", ...],
  "reasoning": "why this classification was made"
}
```

### System Prompt (abbreviated)
```
You are a senior software engineer performing ticket triage.

Classify the ticket as one of:
  feature, bug, config, investigation, performance, security, refactor, documentation

Determine:
  - requires_code_changes: true/false
  - confidence: 0.0 to 1.0
  - root_cause hypothesis if it's a bug
  - which systems are affected

Respond with JSON only.
```

### Decision Gate
- `requires_code_changes = True` → continues to `plan_node`
- `requires_code_changes = False` → routes to `unified_analysis_node` (solution guidance path), **skips code generation entirely**

---

## 3. Agent 1 — Ticket Analyzer (Unified Analysis)

**File**: `src/ticket_to_code/agents/ticket_analyzer.py`  
**Node**: `unified_analysis_node()` in `workflow.py`

### What it does
Two-path node depending on investigation result:

**Path A (code ticket)**: Extracts machine-readable structured requirements from the free-text ticket.

**Path B (non-code ticket)**: Runs RAG queries and generates step-by-step solution guidance (config instructions, troubleshooting steps).

### Input
| Source | Data |
|--------|------|
| `state["ticket"]` | Full ticket object |
| `state["investigation_result"]` | Classification result from Agent 0 |
| RAG engine (Path B only) | Troubleshooting docs, config examples |

### Output (Path A)
`StructuredRequirements` written to `state["requirements"]`:
```python
{
  "functional_requirements": ["Update copyright year to 2026", ...],
  "technical_requirements": ["TypeScript Angular component", ...],
  "edge_cases": ["Handle null text", ...],
  "affected_components": ["Footer", "JATOHeader", ...],
  "non_functional_requirements": ["No layout changes", ...]
}
```

### Output (Path B)
`SolutionGuidance` written to `state["solution_guidance"]`:
```python
{
  "solution_type": "configuration" | "usage" | "troubleshooting",
  "step_by_step_solution": ["Step 1: ...", "Step 2: ..."],
  "configuration_examples": [...],
  "references": [...]
}
```

### System Prompt (Path A, abbreviated)
```
You are a requirements analyst converting user stories into structured requirements.

Extract:
  - functional_requirements: what the system must DO
  - technical_requirements: HOW it must be built (language, framework, patterns)
  - edge_cases: what could go wrong
  - affected_components: which parts of the system change
  - non_functional_requirements: performance, security, UX constraints

Respond with JSON only.
```

---

## 4. Agent 2 — Planning Agent

**File**: `src/ticket_to_code/agents/planning_agent.py`  
**Node**: `plan_node()` in `workflow.py`

### What it does
Decomposes the structured requirements into a list of concrete development tasks. Each task maps to exactly ONE file. The planner also runs RAG queries to understand the existing codebase structure before generating the plan.

### Input
| Source | Data |
|--------|------|
| `state["ticket"]` | Ticket object |
| `state["requirements"]` | Structured requirements from Agent 1 |
| RAG engine | Up to 3 queries → architectural examples from pgvector |

### RAG Queries run by plan_node
```python
Query 1: "existing code structure {affected_components[0:3]}"
Query 2: "similar implementation {technical_requirements[0:2]}"
Query 3: "project structure file organization patterns"
```
Results: up to 30 code examples from pgvector (PostgreSQL, port 5433).

### Output
`ArchitecturalPlan` written to `state["architectural_plan"]`:
```json
{
  "pattern": "Clean Architecture",
  "affected_modules": ["UI Layer"],
  "tasks": [
    {
      "id": "task-1",
      "title": "Update copyright year constant",
      "description": "Change COPYRIGHT_TEXT value in constants file",
      "file_path": "xchange-ui/src/jato/constants/jcommon-constant.ts",
      "task_type": "modify",
      "language": "typescript",
      "dependencies": [],
      "estimated_complexity": 1,
      "requires_testing": true
    }
  ],
  "api_changes": [],
  "database_changes": [],
  "external_dependencies": []
}
```

### System Prompt Rules (key constraints)
```
SCALE TASKS TO TICKET COMPLEXITY:
  • Simple change (constant / label / config)  → 1-2 tasks maximum
  • Bug fix                                    → 2-4 tasks
  • New feature                                → as many as genuinely needed

REAL CODE TASKS ONLY — never create tasks for:
  • "Verify the change works" (not a code task)
  • "Test in browser" (not a code task)

Do NOT invent files or classes that don't exist in the codebase.
Do NOT add backend tasks for a purely frontend change.
```

### Known Weakness
The planner invents **LLM-hallucinated file paths** (e.g., `src/services/CopyrightService.java` for a TypeScript ticket). This is why Agent 2.5 (Localization) exists — it replaces every invented path with a real one.

---

## 5. Agent 2.5 — Localization Agent

**File**: `src/ticket_to_code/agents/localization_agent.py`  
**Node**: `localize_node()` in `workflow.py`

### What it does
**THE MOST CRITICAL AGENT.** Takes the planner's hallucinated file paths and resolves each one to an actual file on disk. The resolved path replaces the planner path before any code is generated.

### Input
| Source | Data |
|--------|------|
| `state["architectural_plan"].tasks` | Planned tasks with invented paths |
| `state["ticket"].title + description` | Raw ticket text → keyword source |
| SQLite AST index | `C:\CC4E\.aviator\index.db` — 39,052 Java symbols |
| Filesystem | Walks `C:\CC4E\` for TypeScript/other files |

### Resolution Strategy (3-phase cascade)

#### Phase 1 — SQLite AST (Java only)
```python
SqliteStore.search_symbols(text=task_keywords, kinds=[], limit=25)
```
Searches the FTS5 full-text index for symbol names matching the task title. Returns file path if a Java class/method is found.  
**Limitation**: Only Java files are indexed. TypeScript/Angular files → Phase 2.

#### Phase 2 — Filesystem with Combined Scoring
When SQLite finds nothing:
1. Walk `C:\CC4E\` (skipping `.git`, `node_modules`, `target`, `dist`, `build`)
2. For each `.ts`, `.js`, `.tsx`, `.vue`, `.java`, `.cs`, `.py`, `.html`, `.scss` file:
   - **Path score**: count keyword matches in the file path
3. Take top-20 path-scored candidates
4. **Content boost**: open each file, count keyword matches in content (up to 6 keywords)
5. Combined score = path_score + content_boost
6. Return file with highest combined score

#### Phase 3 — Content-only scan
If no path matches at all: full content scan, requires ≥ 2 keyword hits to qualify.

### Keyword Extraction
```python
def _extract_keywords(text):
    # Split CamelCase: "jcommonConstants" → ["jcommon", "constants"]
    # Filter stop words: "the", "and", "for", "with", ...
    # Keep words ≥ 4 chars
    # Return set of lowercase keywords
```

Keywords come from **two sources**:
1. Task title + description (planner-generated)
2. Raw ticket title + description (`ticket_context` passed from `localize_node`)

This ensures that ticket-level keywords like `"JATO"`, `"footer"`, `"copyright"` override planner-invented Java class names.

### Output
Each `task.file_path` is replaced with a real disk path. `task.task_type` is set to `"modify"` if the file exists. `task.localization_reason` records why that file was chosen.

---

## 6. Agent 3A/3B — RAG Engine (Context Retrieval)

**File**: `src/ticket_to_code/retrieval/rag_engine.py`  
**Nodes**: `rag_for_tests_node()`, `rag_for_code_node()`

### What it does
Retrieves relevant code examples and documentation from the vector database (pgvector) to give the generators grounded context.

Two **separate** retrieval runs intentionally isolated:

| Branch | Focus | Document Types |
|--------|-------|----------------|
| 3A (test RAG) | Product **behavior** | tests, documentation, business rules |
| 3B (code RAG) | **Architecture** patterns | source_code, interfaces, patterns |

Isolation prevents the code generator from seeing test internals (TDD discipline).

### Data Source
- **pgvector** PostgreSQL: `localhost:5433`, db `postgres`
- Vectors: `models/text-embedding-004` embeddings (Google)
- Indexed from the CC4E project (Java source, docs, existing tests)

### Output
`state["test_rag_context"]` and `state["code_rag_context"]` — lists of `CodeChunk` dicts with:
```python
{"file_path": "...", "content": "...", "type": "source_code", "name": "..."}
```

---

## 7. Agent 4A — Test Generator

**File**: `src/ticket_to_code/agents/code_generator.py` (same class, separate instance)  
**Node**: `generate_tests_node()` in `workflow.py`

### What it does
Generates unit test files — one per planned task — using **only** the behavior RAG context (not architecture context). Test paths are derived from source paths using language-aware rules.

### Test Path Rules
| Source Extension | Test Path |
|---|---|
| `.java` | Replace `src/main/java` → `src/test/java`, add `Test.java` suffix |
| `.ts` / `.tsx` | Add `.spec.ts` / `.spec.tsx` before extension |
| `.js` / `.jsx` | Add `.test.js` / `.test.jsx` |
| `.vue` | Replace `.vue` → `.spec.ts` |
| `.cs` | Replace `Services/` → `Tests/`, `.cs` → `Tests.cs` |

The LLM cannot change the test file path — it is always overridden by the workflow.

### Patch Validation (new)
Before writing the test file to disk, `PatchValidator.validate()` is called. See §9.

---

## 8. Agent 4B — Code Generator

**File**: `src/ticket_to_code/agents/code_generator.py`  
**Node**: `generate_code_node()` in `workflow.py`  
**Class**: `CodeGeneratorAgent`

### What it does
Generates the actual implementation code for each task. The generator's freedom is **strictly constrained** — it acts as a **Senior Maintainer**, not an architect.

### Input
| Source | Data |
|--------|------|
| `task.file_path` | The ONE allowed file (frozen by Localization) |
| `task.task_type` | `"modify"` or `"create"` |
| `task.title` + `task.description` | What to change |
| `state["requirements"]` | Structured requirements |
| `state["code_rag_context"]` | Architecture patterns (reference only) |
| `existing_content` | Full current file content (read from disk for modify tasks) |

### System Prompt (full intent)
```
You are a Senior Maintainer working on an enterprise production codebase.

YOUR ROLE IS NOT ARCHITECT. YOUR ROLE IS SURGICAL MODIFIER.

ABSOLUTE RULES — NEVER VIOLATE:
1. ONLY modify the ONE file explicitly specified in the task. Period.
2. NEVER create new files, new classes, new interfaces, or new helpers.
3. NEVER refactor, rename, or restructure unrelated code.
4. NEVER improve architecture, add design patterns, or reorganize imports.
5. Apply the SMALLEST change that satisfies the requirement.
6. Preserve ALL existing formatting, indentation, comments, and structure.
7. USE ONLY code patterns already present in the existing file.
8. MODIFY task → return COMPLETE file with only minimal targeted change applied.
9. CREATE task → create only the single file specified.

MINDSET: Think diff, not rewrite. Touch only what must change.
```

### User Prompt: FROZEN SCOPE BLOCK
Every generation request includes this constraint header:
```
╔══════════════════════════════════════════════════════╗
║  FROZEN MODIFICATION SCOPE — DO NOT DEVIATE          ║
╠══════════════════════════════════════════════════════╣
║  ALLOWED FILE   : xchange-ui/src/jato/header/...    ║
║  OPERATION      : MODIFY                             ║
║  NEW FILES      : NOT ALLOWED                        ║
║  NEW CLASSES    : NOT ALLOWED                        ║
║  REFACTORING    : NOT ALLOWED                        ║
║  EXTRA HELPERS  : NOT ALLOWED                        ║
╚══════════════════════════════════════════════════════╝
```
The planner and localization agent have already frozen what file to touch. The generator has zero authority to deviate.

### For MODIFY tasks: existing file is passed
```
EXISTING FILE CONTENT — modify this file, do NOT recreate from scratch:
[full file content up to 8000 chars]

INSTRUCTION: Find the minimal insertion/replacement needed. Return the
COMPLETE file with only that change applied. All other lines must be
byte-for-byte identical.
```

### Output (JSON)
```json
{
  "code": "...complete file content with minimal change...",
  "imports": [],
  "documentation": "Changed getCopyrightText() to return constant directly"
}
```

### Language-specific guidelines passed in system prompt
| Language | Key rules |
|---|---|
| C# | PascalCase, XML docs, async/await, nullable types |
| TypeScript | Strict types, interfaces, const, arrow functions |
| Python | PEP8, type hints, docstrings, context managers |
| Java | (uses default — follows existing patterns) |

---

## 9. Patch Validator (Scope Guard)

**File**: `src/ticket_to_code/agents/code_generator.py`  
**Class**: `PatchValidator`  
**Called from**: `generate_code_node()` and `generate_tests_node()` — **before** any file is written

### What it does
Enterprise-grade patch validation: checks that the LLM output stays within the frozen scope defined by the planner and localization agent.

### Four Checks

| Check | What it verifies | Action on failure |
|---|---|---|
| 1 — File scope | `generated.file_path` matches `task.file_path` | Block write, log violation |
| 2 — Change budget | For MODIFY tasks: `difflib` change ratio ≤ 80% of original | Block write, log warning |
| 3 — Suspicious patterns | No `// new file`, `// file:` markers in a MODIFY task | Block write, log violation |
| (future) 4 — Method scope | Only target method changed | Extensible |

### Metrics logged on every validation
```python
{
  "file_match": True,
  "original_line_count": 245,
  "new_line_count": 247,
  "change_ratio": 0.012,   # 1.2% of lines changed — minimal, correct
  "changed_blocks": 1
}
```

### Decision
- `passed = True` → file is written to disk
- `passed = False` → **file is NOT written**, violation is logged, processing continues (does not crash)

### Why 80% threshold?
A "modify" task that changes 80%+ of lines is almost certainly a full rewrite, not a targeted patch. Legitimate modifications rarely exceed 15-20% even for complex bug fixes.

---

## 10. Agent 5 — Build Executor

**File**: `src/ticket_to_code/execution/base_executor.py` (+ language-specific executors)  
**Node**: `build_node()` in `workflow.py`

### What it does
Runs the project's native build tool to verify generated code compiles.

### Language Detection
```
pom.xml / build.gradle  → Maven/Gradle
*.sln / *.csproj        → MSBuild (.NET)
setup.py / pyproject.toml → Python (no compile step)
package.json            → npm
```

### Output
`BuildResult` → `state["build_result"]` with `status: success | failure` and any compiler errors.

### On failure
`fix_build_node` retries with build errors fed back to the code generator (up to `max_retry_attempts`).

---

## 11. Agent 6 — Test Executor

**File**: Same execution engine  
**Node**: `test_node()` in `workflow.py`

### What it does
Runs the project's test suite against the generated code.

### On failure
`fix_test_node` retries with test failures fed back (up to `max_retry_attempts`).

---

## 12. Data Sources Reference

| Store | Technology | Location | What's in it | Used by |
|---|---|---|---|---|
| SQLite AST index | FTS5 SQLite | `C:\CC4E\.aviator\index.db` | 39,052 Java symbols (class names, method names, file paths) | Localization (Phase 1) |
| pgvector | PostgreSQL 15 | `localhost:5433` | Code chunks + embeddings from CC4E (Java, docs) | RAG engine (all retrieval) |
| Neo4j | Graph DB | `localhost:7687` | Dependency graph (class → class, method → method) | Localization (graph traversal, optional) |
| Filesystem | `C:\CC4E\` | Local disk | All source files including TypeScript/Angular | Localization (Phase 2 fallback) |
| LLM | Gemini 2.0 Flash | Vertex AI `europe-west4` | — | All agents |
| Embedding model | `text-embedding-004` | Google AI | — | RAG indexing + retrieval |

---

## 13. Why Each Constraint Exists

This section explains the reasoning behind every non-obvious rule.

### Why does Localization use filesystem scoring instead of just SQLite?
The SQLite AST index is **Java-only**. CC4E's Angular/TypeScript frontend (`xchange-ui/`) is not indexed. Without filesystem fallback, TypeScript tickets would always fail to find the correct file.

### Why is ticket text passed to Localization (not just task text)?
The Planner invents Java class names (e.g., `CopyrightYearService`) that don't exist. The localization keywords derived from these names don't match TypeScript files. Ticket text contains domain keywords (`"JATO"`, `"footer"`, `"copyright"`) that DO match the real files.

### Why is the Generator now a "Senior Maintainer" not a "Senior Engineer"?
All LLMs — Gemini, GPT-4, Claude, DeepSeek — default to "design clean solution" mode. They invent helper classes, refactor unrelated code, add new files for architecture reasons. This is correct behaviour for greenfield work but catastrophically wrong for surgical maintenance of an enterprise codebase. The prompt persona change forces the model into minimal-diff mode.

### Why is existing file content passed to the Generator?
Without it, the LLM generates the file from scratch using only its training knowledge and RAG snippets. This leads to: missing imports, wrong class structure, hallucinated method signatures. Passing the real file means the LLM sees exactly what exists and can make a one-line change instead of a full rewrite.

### Why is PatchValidator a separate class (not inline in the workflow)?
The same validator runs for both code generation and test generation. A class makes it reusable and testable independently. It also makes the 80% threshold easily configurable without touching workflow logic.

### Why does the Planner not just ask the user for the exact file?
The system is autonomous — the user only provides a ticket. The planner is an intermediate step that breaks ambiguous natural language into a concrete task list. Localization then grounds those tasks in reality. Both steps are necessary because neither alone is sufficient.

### Why two separate RAG retrievals (3A for tests, 3B for code)?
TDD discipline: tests should be written against the **behaviour contract** (what the system does), not the implementation. If the test generator saw architecture patterns, it would write implementation-coupled tests. By keeping contexts separate, tests can be generated independently and will catch implementation deviations.

---

## Summary: Decision Responsibility Map

```
Who decides WHAT to change?
  → Planning Agent (LLM, based on ticket + requirements)

Who decides WHICH REAL FILE to change?
  → Localization Agent (AST index + filesystem scoring)

Who decides WHAT the code change looks like?
  → Code Generator (LLM, constrained to 1 file, minimal diff)

Who verifies the change is in scope before writing?
  → Patch Validator (deterministic, rule-based)

Who verifies the change compiles and passes tests?
  → Build + Test Executor (native toolchain)
```

**The key insight**: LLMs are good at understanding intent and generating syntax-correct code. They are bad at scope discipline. Every constraint in this system exists to compensate for that specific weakness while preserving everything LLMs are good at.
