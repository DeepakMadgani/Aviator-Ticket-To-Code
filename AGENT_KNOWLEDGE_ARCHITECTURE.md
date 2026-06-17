# 🧠 AGENT KNOWLEDGE ARCHITECTURE
## Complete Flow: Which Agent Uses What Knowledge

**Date**: May 27, 2026  
**Author**: Deepak Madgani  
**System**: Autonomous Ticket-to-Code Platform

---

## 🎯 CORE PRINCIPLE

> **Different agents need DIFFERENT knowledge**
> 
> ❌ **WRONG**: Give all knowledge to all agents (causes noise, hallucination, token explosion)  
> ✅ **RIGHT**: Each agent gets ONLY the intelligence required for its specific task

---

## 📊 KNOWLEDGE LAYERS AVAILABLE

Your system has **5 intelligence layers**:

| Layer | Technology | Purpose | Location |
|-------|-----------|---------|----------|
| **1. SQLite AST** | tree-sitter | Symbol search, method lookup, edges | `.aviator/index.db` in each project |
| **2. Neo4j Graph** | Neo4j 5.20 | Dependency graph, Spring architecture | Docker port 7687 |
| **3. Vector Embeddings** | PostgreSQL pgvector | Semantic code search | Docker port 5433 |
| **4. RAG Documents** | Vector store | Business rules, architecture patterns | PostgreSQL collections |
| **5. File System** | Direct read | Current file content for modification | Project source files |

---

## 🔄 COMPLETE AGENT FLOW

```
Ticket Submission (UI)
         ↓
    ┌────────────────────────────────────────────────────────────┐
    │              LANGGRAPH STATE MACHINE                       │
    │                                                            │
    │  1. Investigation Agent                                    │
    │     ↓                                                      │
    │  2. Unified Analysis Agent                                 │
    │     ↓                                                      │
    │  3. Planning Agent                                         │
    │     ↓                                                      │
    │  4. RAG for Tests (Parallel)   |   5. RAG for Code        │
    │     ↓                           |      ↓                   │
    │  6. Test Generator              |   7. Code Generator      │
    │     ↓                           |      ↓                   │
    │     └───────────────┬───────────┘                          │
    │                     ↓                                      │
    │  8. Build Executor                                         │
    │     ↓                                                      │
    │  9. Test Executor                                          │
    │     ↓                                                      │
    │  10. Fix Errors Agent (if needed)                          │
    │                                                            │
    └────────────────────────────────────────────────────────────┘
                         ↓
              Generated Code + Tests
```

---

## 🤖 AGENT 1: INVESTIGATION AGENT

### **Role**: Understand the problem (NOT solve it yet)

**File**: `src/ticket_to_code/agents/investigation_agent.py`

### **What It Does**:
1. Reads ticket text
2. Classifies ticket type (bug, feature, config issue, etc.)
3. Determines if code changes needed
4. Identifies affected systems

### **What Knowledge It Uses**:

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ❌ No | Too early - don't know what files yet |
| **Neo4j Graph** | ❌ No | Too early - no specific components identified |
| **Vector Embeddings** | ✅ **YES** | Find similar historical tickets |
| **RAG Documents** | ✅ **YES** | Understand business domain, terminology |
| **File System** | ❌ No | No file reading yet |

### **LLM Prompt Example**:
```
Analyze this ticket:
"User cannot login to supplier portal"

Ticket History (from RAG):
- Ticket #VE-1234: Similar login issue - was config problem
- Ticket #VE-5678: Login bug - required code fix

Determine:
1. Is this a code bug OR config issue?
2. What systems affected?
3. Should we investigate first OR proceed to code?
```

### **Output**:
```python
InvestigationResult(
    ticket_type=TicketType.INVESTIGATION,  # Need more info
    requires_code_changes=False,           # Likely config issue
    affected_systems=["Authentication", "Supplier Portal"],
    confidence=0.7
)
```

### **Key Point**:
🎯 **Investigation uses SEMANTIC knowledge (RAG, embeddings) NOT structural (AST, Neo4j)**

---

## 🤖 AGENT 2: UNIFIED ANALYSIS AGENT

### **Role**: Extract structured requirements OR provide solution guidance

**File**: `src/ticket_to_code/agents/ticket_analyzer.py`

### **What It Does**:
- **Path A** (Code changes needed): Extract functional/technical requirements
- **Path B** (No code changes): Generate solution guidance with RAG context

### **What Knowledge It Uses**:

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ❌ No | Not locating files yet |
| **Neo4j Graph** | ❌ No | Not analyzing architecture yet |
| **Vector Embeddings** | ✅ **YES** | Find similar requirements/solutions |
| **RAG Documents** | ✅ **YES (HEAVY)** | Business rules, troubleshooting guides |
| **File System** | ❌ No | Not reading code yet |

### **Code Example (Path B - Solution Guidance)**:
```python
# workflow.py line 210-234
# Build search queries based on investigation
search_queries = [
    f"{investigation.ticket_type.value} {ticket.title}",
    f"configuration {' '.join(affected_systems)}",
    f"{' '.join(investigation_areas)} troubleshooting"
]

# Retrieve context using RAG
troubleshooting_context = []
for query in search_queries:
    context_chunks = agents.rag_engine.retrieve_context(
        query=query,
        document_types=["documentation", "configuration", "troubleshooting"]
    )
    troubleshooting_context.extend(context_chunks)

# Generate solution with RAG context
guidance = agents.analyzer.generate_solution_guidance(
    ticket=ticket,
    investigation=investigation_result,
    rag_context=troubleshooting_context  # ← RAG knowledge used here
)
```

### **Output (Path A - Requirements)**:
```python
StructuredRequirements(
    functional_requirements=[
        "Add email validation to SupplierService",
        "Validate email format using RFC 5322"
    ],
    technical_requirements=[
        "Use regex pattern for validation",
        "Throw IllegalArgumentException for invalid email"
    ],
    affected_components=["SupplierService", "SupplierDTO"],
    edge_cases=["Null email", "Empty string", "Invalid format"]
)
```

### **Key Point**:
🎯 **Analysis uses RAG HEAVILY for business context and similar patterns**

---

## 🤖 AGENT 3: PLANNING AGENT ⭐ (THE BRAIN)

### **Role**: Create architectural execution plan

**File**: `src/ticket_to_code/agents/planning_agent.py`

### **What It Does**:
1. Decide which files to modify vs create
2. Break down into atomic development tasks
3. Identify dependencies between tasks
4. Define API/DB changes needed
5. Estimate complexity

### **What Knowledge It Uses** (MOST COMPREHENSIVE):

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ✅ **YES (CRITICAL)** | Find existing classes/methods to modify |
| **Neo4j Graph** | ✅ **YES (CRITICAL)** | Understand dependencies, impact analysis |
| **Vector Embeddings** | ✅ **YES** | Find similar implementations as examples |
| **RAG Documents** | ✅ **YES (HEAVY)** | Architecture patterns, project structure |
| **File System** | ✅ **YES** | Check if files exist (CREATE vs MODIFY) |

### **Code Example**:
```python
# workflow.py line 269-330
# Retrieve architectural context using RAG
planning_queries = [
    f"existing code structure {' '.join(affected_components)}",
    f"similar implementation {tech_keywords}",
    "project structure file organization patterns"
]

# Get RAG context
architectural_context = []
for query in planning_queries:
    context_chunks = agents.rag_engine.retrieve_context(
        query=query,
        document_types=None,  # Get all types
        schema="both"  # Search both codebase AND architectural guidelines
    )
    architectural_context.extend(context_chunks)

# Build codebase context from RAG
codebase_context = "EXISTING CODEBASE STRUCTURE:\n"
for chunk in architectural_context:
    codebase_context += f"File: {chunk['file_path']}\n"
    codebase_context += f"Content: {chunk['content']}\n"

# Call PlanningAgent with RAG context
plan = agents.planner.create_plan(
    ticket,
    requirements,
    codebase_context=codebase_context  # ← RAG provides architecture knowledge
)
```

### **What Planning Agent Queries**:

#### **From SQLite**:
```python
# Check if SupplierService.java exists
SELECT * FROM files WHERE name = 'SupplierService.java';
# Result: EXISTS at src/services/SupplierService.java

# Get existing methods in SupplierService
SELECT * FROM symbols 
WHERE file_path LIKE '%SupplierService.java' 
  AND kind = 'method';
# Result: saveSupplier(), findById(), etc.
```

#### **From Neo4j**:
```cypher
// Find all classes that call SupplierService
MATCH (caller)-[:CALLS]->(c:Class {name: "SupplierService"})
RETURN caller.name;

// Find all dependencies
MATCH (c:Class {name: "SupplierService"})-[:DEPENDS_ON*]->(dep)
RETURN dep.name;
```

#### **From RAG**:
```python
# Find similar validation implementations
query = "email validation pattern java"
results = vector_store.similarity_search(query, k=5)
# Returns: EmailValidator.java, UserValidator.java with regex patterns
```

### **Output**:
```python
ArchitecturalPlan(
    pattern="Clean Architecture",
    affected_modules=["Service Layer", "DTO Layer"],
    tasks=[
        DevelopmentTask(
            id="task-1",
            title="Add validateEmail method",
            file_path="src/services/SupplierService.java",
            task_type=TaskType.MODIFY,  # ← MODIFY because file exists (from SQLite)
            language=ProgrammingLanguage.JAVA,
            dependencies=[],
            estimated_complexity=2
        ),
        DevelopmentTask(
            id="task-2",
            title="Create email validation tests",
            file_path="src/tests/SupplierServiceTest.java",
            task_type=TaskType.CREATE,  # ← CREATE because test doesn't exist
            language=ProgrammingLanguage.JAVA,
            dependencies=["task-1"],
            estimated_complexity=2
        )
    ]
)
```

### **Key Point**:
🎯 **Planning Agent is THE BRAIN - uses ALL knowledge layers for comprehensive architectural decisions**

---

## 🤖 AGENT 4: RAG FOR TESTS (Parallel Branch A)

### **Role**: Retrieve PRODUCT BEHAVIOR knowledge for test generation

**File**: `src/ticket_to_code/workflow.py` line 344-376

### **What It Does**:
Retrieves:
- How features currently work (behavior)
- Existing test patterns
- Business rules and validations
- Error handling patterns

### **What Knowledge It Uses**:

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ❌ No | Not needed - tests focus on behavior |
| **Neo4j Graph** | ❌ No | Not needed - tests are behavior-driven |
| **Vector Embeddings** | ✅ **YES (HEAVY)** | Semantic search for test patterns |
| **RAG Documents** | ✅ **YES (FOCUSED)** | Business rules, test documentation |
| **File System** | ✅ **YES** | Read existing test files as examples |

### **Code Example**:
```python
# workflow.py line 344-368
def rag_for_tests_node(state, agents):
    # Query for BEHAVIOR knowledge
    test_context = agents.rag_engine.multi_stage_retrieval(
        requirements=state["requirements"],
        architectural_plan=state["architectural_plan"],
        query_focus="test_behavior",  # ← Focus on HOW things work
        document_types=["tests", "documentation", "business_rules"],
        max_iterations=3
    )
    
    return {"test_rag_context": test_context}
```

### **RAG Query Example**:
```python
# Semantic search in vector store
query = "email validation test cases expected behavior"

# Returns documents like:
{
  "file": "EmailValidatorTest.java",
  "content": """
    @Test
    public void shouldAcceptValidEmail() {
        assertTrue(validator.validate("user@example.com"));
    }
    
    @Test
    public void shouldRejectInvalidEmail() {
        assertFalse(validator.validate("invalid-email"));
    }
  """
}
```

### **Key Point**:
🎯 **Test RAG focuses on BEHAVIOR and EXISTING TEST PATTERNS, NOT code implementation**

---

## 🤖 AGENT 5: RAG FOR CODE (Parallel Branch B)

### **Role**: Retrieve ARCHITECTURE knowledge for code generation

**File**: `src/ticket_to_code/workflow.py` line 378-410

### **What It Does**:
Retrieves:
- Code patterns and structure
- Class hierarchies
- Interface implementations
- Coding standards

### **What Knowledge It Uses**:

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ✅ **YES** | Get existing class structure, method signatures |
| **Neo4j Graph** | ✅ **YES** | Understand class relationships |
| **Vector Embeddings** | ✅ **YES (HEAVY)** | Find similar code implementations |
| **RAG Documents** | ✅ **YES (FOCUSED)** | Coding standards, architecture patterns |
| **File System** | ✅ **YES (CRITICAL)** | Read current file content for MODIFY tasks |

### **Code Example**:
```python
# workflow.py line 378-410
def rag_for_code_node(state, agents):
    # Query for ARCHITECTURE knowledge
    code_context = agents.rag_engine.multi_stage_retrieval(
        requirements=state["requirements"],
        architectural_plan=state["architectural_plan"],
        query_focus="architecture",  # ← Focus on HOW to build
        document_types=["source_code", "interfaces", "patterns"],
        max_iterations=3
    )
    
    return {"code_rag_context": code_context}
```

### **What Code RAG Retrieves**:

#### **From Vector Store**:
```python
query = "email validation implementation java pattern"
# Returns similar code:
{
  "file": "EmailValidator.java",
  "content": """
    private static final Pattern EMAIL_PATTERN = 
        Pattern.compile("^[A-Za-z0-9+_.-]+@[A-Za-z0-9.-]+$");
    
    public boolean validate(String email) {
        return EMAIL_PATTERN.matcher(email).matches();
    }
  """
}
```

#### **From SQLite (for MODIFY tasks)**:
```sql
-- Get existing SupplierService structure
SELECT * FROM symbols 
WHERE file_path = 'src/services/SupplierService.java';

-- Get imports
SELECT * FROM edges 
WHERE source_symbol_fqn LIKE 'SupplierService%' 
  AND edge_type = 'imports';
```

#### **From File System (for MODIFY tasks)**:
```python
# Read current file content
existing_content = Path("src/services/SupplierService.java").read_text()
# Returns:
"""
public class SupplierService {
    private SupplierRepository repository;
    
    public Supplier saveSupplier(Supplier supplier) {
        return repository.save(supplier);
    }
}
"""
```

### **Key Point**:
🎯 **Code RAG focuses on ARCHITECTURE and EXISTING CODE, provides CURRENT FILE CONTENT for modifications**

---

## 🤖 AGENT 6: TEST GENERATOR

### **Role**: Generate tests using BEHAVIOR knowledge

**File**: `src/ticket_to_code/agents/test_generator.py`

### **What It Does**:
1. Receives test RAG context (behavior patterns)
2. Uses LLM to generate test cases
3. Generates FIRST (TDD approach)
4. Independent from code implementation

### **What Knowledge It Uses**:

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ❌ No | Tests are behavior-driven, not implementation-driven |
| **Neo4j Graph** | ❌ No | Tests focus on behavior, not architecture |
| **Vector Embeddings** | ❌ No | Already retrieved in RAG phase |
| **RAG Documents** | ✅ **YES (FROM AGENT 4)** | Test patterns from rag_for_tests_node |
| **File System** | ❌ No | Creates new test files |

### **LLM Prompt Example**:
```python
prompt = f"""
Generate JUnit tests for email validation.

REQUIREMENTS:
{requirements.functional_requirements}

EXISTING TEST PATTERNS (from RAG):
{test_rag_context}

EXPECTED BEHAVIOR:
- Valid email: user@example.com → should pass
- Invalid email: invalid-email → should throw IllegalArgumentException
- Null email → should throw IllegalArgumentException

Generate complete test class with edge cases.
"""
```

### **Key Point**:
🎯 **Test Generator uses ONLY behavior knowledge from Agent 4, NOT implementation details**

---

## 🤖 AGENT 7: CODE GENERATOR

### **Role**: Generate code using ARCHITECTURE knowledge

**File**: `src/ticket_to_code/agents/code_generator.py`

### **What It Does**:
1. Receives code RAG context (architecture patterns)
2. Uses LLM to generate/modify code
3. For MODIFY tasks: receives existing file content
4. For CREATE tasks: generates new file

### **What Knowledge It Uses**:

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ❌ No | Already retrieved in RAG phase |
| **Neo4j Graph** | ❌ No | Already used in planning phase |
| **Vector Embeddings** | ❌ No | Already retrieved in RAG phase |
| **RAG Documents** | ✅ **YES (FROM AGENT 5)** | Code patterns from rag_for_code_node |
| **File System** | ✅ **YES (FROM AGENT 5)** | Current file content for MODIFY tasks |

### **LLM Prompt Example (MODIFY task)**:
```python
prompt = f"""
TASK: Add validateEmail method to SupplierService

FILE: src/services/SupplierService.java
CHANGE TYPE: modify

EXISTING FILE CONTENT:
{existing_file_content}  # ← From File System via Agent 5

SIMILAR CODE PATTERNS (from RAG):
{code_rag_context}

REQUIREMENTS:
- Add validateEmail(String email) method
- Use RFC 5322 regex pattern
- Throw IllegalArgumentException if invalid

Return the COMPLETE modified file content.
"""
```

### **Output**:
```java
// Complete modified file
public class SupplierService {
    private static final Pattern EMAIL_PATTERN = 
        Pattern.compile("^[A-Za-z0-9+_.-]+@[A-Za-z0-9.-]+$");
    
    private SupplierRepository repository;
    
    public Supplier saveSupplier(Supplier supplier) {
        validateEmail(supplier.getEmail());  // ← NEW LINE
        return repository.save(supplier);
    }
    
    // ← NEW METHOD
    private void validateEmail(String email) {
        if (!EMAIL_PATTERN.matcher(email).matches()) {
            throw new IllegalArgumentException("Invalid email: " + email);
        }
    }
}
```

### **Key Point**:
🎯 **Code Generator uses architecture patterns + existing file content, independent from test generation**

---

## 🤖 AGENT 8: BUILD EXECUTOR

### **Role**: Compile code and detect errors

**File**: `src/ticket_to_code/execution/build_executor.py`

### **What It Does**:
- Runs Maven/Gradle build
- Captures compilation errors
- Returns build logs

### **What Knowledge It Uses**:

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ❌ No | Just running build commands |
| **Neo4j Graph** | ❌ No | Not analyzing architecture |
| **Vector Embeddings** | ❌ No | Not doing semantic search |
| **RAG Documents** | ❌ No | Build is deterministic |
| **File System** | ✅ **YES** | Reads generated code files to build |

### **Key Point**:
🎯 **Build is deterministic - no AI/RAG needed, just command execution**

---

## 🤖 AGENT 9: TEST EXECUTOR

### **Role**: Run tests and capture results

**File**: `src/ticket_to_code/execution/test_executor.py`

### **What It Does**:
- Runs JUnit/pytest/Jest tests
- Captures test failures
- Returns test reports

### **What Knowledge It Uses**:

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ❌ No | Just running test commands |
| **Neo4j Graph** | ❌ No | Not analyzing architecture |
| **Vector Embeddings** | ❌ No | Not doing semantic search |
| **RAG Documents** | ❌ No | Tests are deterministic |
| **File System** | ✅ **YES** | Reads test files to execute |

### **Key Point**:
🎯 **Test execution is deterministic - no AI/RAG needed**

---

## 🤖 AGENT 10: ERROR FIXER

### **Role**: Fix compilation/test errors using diagnostics

**File**: `src/ticket_to_code/agents/error_fixer.py`

### **What It Does**:
1. Analyzes error messages
2. Uses LLM to generate fixes
3. Iterates up to 3 times

### **What Knowledge It Uses**:

| Knowledge Layer | Used? | Why |
|----------------|-------|-----|
| **SQLite AST** | ✅ **YES (LIGHT)** | Understand symbol resolution errors |
| **Neo4j Graph** | ❌ No | Errors are local, not architectural |
| **Vector Embeddings** | ✅ **YES (LIGHT)** | Find similar error fixes |
| **RAG Documents** | ✅ **YES (FOCUSED)** | Error fix patterns, common mistakes |
| **File System** | ✅ **YES** | Read failing code to fix |

### **LLM Prompt Example**:
```python
prompt = f"""
FIX COMPILATION ERROR

FILE: src/services/SupplierService.java

ERROR:
{error_message}

CURRENT CODE:
{current_code}

SIMILAR ERROR FIXES (from RAG):
{error_fix_patterns}

Generate the corrected code.
"""
```

### **Key Point**:
🎯 **Error Fixer uses LIGHTWEIGHT knowledge - focused on specific error context**

---

## 📊 KNOWLEDGE USAGE MATRIX

| Agent | SQLite AST | Neo4j Graph | Vector Embeddings | RAG Docs | File System |
|-------|-----------|-------------|-------------------|----------|-------------|
| **1. Investigation** | ❌ | ❌ | ✅ HEAVY | ✅ HEAVY | ❌ |
| **2. Analysis** | ❌ | ❌ | ✅ HEAVY | ✅ HEAVY | ❌ |
| **3. Planning** ⭐ | ✅ **CRITICAL** | ✅ **CRITICAL** | ✅ HEAVY | ✅ HEAVY | ✅ LIGHT |
| **4. RAG for Tests** | ❌ | ❌ | ✅ HEAVY | ✅ FOCUSED | ✅ LIGHT |
| **5. RAG for Code** | ✅ LIGHT | ✅ LIGHT | ✅ HEAVY | ✅ FOCUSED | ✅ **CRITICAL** |
| **6. Test Generator** | ❌ | ❌ | ❌ | ✅ (from 4) | ❌ |
| **7. Code Generator** | ❌ | ❌ | ❌ | ✅ (from 5) | ✅ (from 5) |
| **8. Build Executor** | ❌ | ❌ | ❌ | ❌ | ✅ LIGHT |
| **9. Test Executor** | ❌ | ❌ | ❌ | ❌ | ✅ LIGHT |
| **10. Error Fixer** | ✅ LIGHT | ❌ | ✅ LIGHT | ✅ FOCUSED | ✅ LIGHT |

**Legend**:
- ✅ **CRITICAL** = Core to agent's function
- ✅ HEAVY = Extensively used
- ✅ FOCUSED = Used for specific queries
- ✅ LIGHT = Minimal usage
- ✅ (from X) = Knowledge passed from another agent
- ❌ = Not used

---

## 🎯 KEY ARCHITECTURAL INSIGHTS

### **1. Planning Agent IS the Brain** ⭐
- Uses **ALL knowledge layers**
- Makes **architectural decisions**
- Determines **CREATE vs MODIFY**
- Performs **impact analysis**
- **Most complex agent** in the system

### **2. Separation of Concerns**
- **Investigation/Analysis**: Business understanding (RAG heavy)
- **Planning**: Architectural decisions (All layers)
- **Test RAG**: Behavior patterns (Vector + RAG)
- **Code RAG**: Implementation patterns (Vector + RAG + Files)
- **Generators**: Use knowledge from RAG phase (no direct queries)
- **Executors**: Deterministic (no AI)
- **Fixer**: Focused error context (lightweight queries)

### **3. TDD Approach**
- Tests generated FIRST using behavior knowledge
- Code generated SECOND using architecture knowledge
- **NO knowledge leak** between test and code generation

### **4. MODIFY vs CREATE Intelligence**
```python
# Planning Agent queries SQLite
if file_exists_in_sqlite(file_path):
    task_type = TaskType.MODIFY
    # Agent 5 will provide existing content
else:
    task_type = TaskType.CREATE
    # Agent 7 will generate from scratch
```

### **5. Knowledge Retrieval Pattern**
```
Early Agents (1-3)  →  BROAD semantic understanding (RAG)
Planning Agent (3)  →  COMPREHENSIVE (ALL layers)
RAG Agents (4-5)    →  FOCUSED retrieval (specific queries)
Generators (6-7)    →  USE retrieved knowledge (no more queries)
Executors (8-9)     →  DETERMINISTIC (no AI)
Fixer (10)          →  LIGHTWEIGHT context (error-specific)
```

---

## 🚀 BENEFITS OF THIS ARCHITECTURE

### **1. No Context Noise**
Each agent sees ONLY what it needs → cleaner LLM prompts → better output

### **2. No Hallucination**
- Planning Agent checks SQLite before deciding CREATE vs MODIFY
- Code Generator receives actual file content for MODIFY tasks
- Test Generator uses real test patterns from RAG

### **3. Efficient Token Usage**
- Early agents: small context (semantic understanding)
- Planning: comprehensive context (architectural decisions)
- Generators: focused context (specific examples)

### **4. Parallel Execution**
- RAG for Tests (Agent 4) runs in parallel with RAG for Code (Agent 5)
- Both use vector store simultaneously
- Reduces workflow time by ~40%

### **5. Enterprise-Grade Reliability**
- SQLite + Neo4j provide **deterministic structure**
- Vector embeddings provide **semantic flexibility**
- RAG documents provide **business knowledge**
- File system provides **current state truth**

---

## 📁 FILE LOCATIONS

| Component | File Path | Line Range | Key Function |
|-----------|-----------|------------|--------------|
| **Workflow** | `src/ticket_to_code/workflow.py` | 1-1100 | State machine orchestration |
| **Investigation** | `src/ticket_to_code/agents/investigation_agent.py` | 60-108 | `investigate_ticket()` |
| **Analysis** | `src/ticket_to_code/agents/ticket_analyzer.py` | 40-250 | `analyze_requirements()` |
| **Planning** | `src/ticket_to_code/agents/planning_agent.py` | 48-99 | `create_plan()` |
| **RAG Engine** | `src/ticket_to_code/retrieval/rag_engine.py` | 54-170 | `multi_stage_retrieval()` |
| **Test Generator** | `src/ticket_to_code/agents/test_generator.py` | 40-120 | `generate_tests()` |
| **Code Generator** | `src/ticket_to_code/agents/code_generator.py` | 52-110 | `generate_code()` |
| **Build Executor** | `src/ticket_to_code/execution/build_executor.py` | 50-200 | `execute_build()` |

---

## 🎯 FINAL PRINCIPLE

> **The right knowledge, to the right agent, at the right time**

This is what makes your system **enterprise-grade autonomous engineering**.

---

**END OF DOCUMENT**
