# 🔄 COMPLETE AGENT FLOW WITH KNOWLEDGE USAGE
## Visual Data Flow: Ticket → Generated Code

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                      USER SUBMITS TICKET                          ┃
┃   "Add email validation to SupplierService"                       ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                                 ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ AGENT 1: INVESTIGATION                                            ┃
┃ File: investigation_agent.py                                      ┃
┃                                                                   ┃
┃ Knowledge Used:                                                   ┃
┃   ✅ Vector Embeddings (pgvector)                                ┃
┃      Query: "email validation similar tickets"                    ┃
┃      Result: Found 3 similar tickets - 2 were code, 1 config     ┃
┃                                                                   ┃
┃   ✅ RAG Documents                                                ┃
┃      Query: "SupplierService business domain"                     ┃
┃      Result: SupplierService manages supplier data validation    ┃
┃                                                                   ┃
┃ LLM Call:                                                         ┃
┃   Prompt: "Is this a code bug, feature, or config issue?"        ┃
┃   Context: Similar tickets + business domain                      ┃
┃                                                                   ┃
┃ Output:                                                           ┃
┃   ticket_type: FEATURE_REQUEST                                    ┃
┃   requires_code_changes: TRUE                                     ┃
┃   affected_systems: ["SupplierService"]                           ┃
┃   confidence: 0.85                                                ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                                 ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ AGENT 2: UNIFIED ANALYSIS                                         ┃
┃ File: ticket_analyzer.py                                          ┃
┃                                                                   ┃
┃ Knowledge Used:                                                   ┃
┃   ✅ Vector Embeddings                                            ┃
┃      Query: "email validation requirements examples"              ┃
┃      Result: RFC 5322 standard, regex patterns                    ┃
┃                                                                   ┃
┃   ✅ RAG Documents                                                ┃
┃      Query: "validation best practices java"                      ┃
┃      Result: Throw exceptions, use regex, validate early          ┃
┃                                                                   ┃
┃ LLM Call:                                                         ┃
┃   Prompt: "Extract structured requirements from ticket"           ┃
┃   Context: Validation patterns + best practices                   ┃
┃                                                                   ┃
┃ Output:                                                           ┃
┃   functional_requirements:                                        ┃
┃     - "Validate email format before saving supplier"              ┃
┃     - "Use RFC 5322 compliant regex"                              ┃
┃   technical_requirements:                                         ┃
┃     - "Add validateEmail(String) method"                          ┃
┃     - "Throw IllegalArgumentException if invalid"                 ┃
┃   affected_components: ["SupplierService", "SupplierDTO"]         ┃
┃   edge_cases: ["null email", "empty string", "special chars"]     ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                                 ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ AGENT 3: PLANNING (THE BRAIN) ⭐⭐⭐                              ┃
┃ File: planning_agent.py                                           ┃
┃                                                                   ┃
┃ Knowledge Used (COMPREHENSIVE):                                   ┃
┃                                                                   ┃
┃ 1️⃣ SQLite AST Query:                                            ┃
┃    SELECT * FROM files WHERE name = 'SupplierService.java';      ┃
┃    Result: EXISTS at src/services/SupplierService.java           ┃
┃                                                                   ┃
┃    SELECT * FROM symbols                                          ┃
┃    WHERE file_path LIKE '%SupplierService%'                       ┃
┃      AND kind = 'method';                                         ┃
┃    Result: saveSupplier(), findById(), deleteSupplier()           ┃
┃                                                                   ┃
┃    Decision: task_type = MODIFY (file exists)                     ┃
┃                                                                   ┃
┃ 2️⃣ Neo4j Graph Query:                                           ┃
┃    MATCH (c:Class {name: "SupplierService"})-[:CALLS]->(m)       ┃
┃    RETURN m.name;                                                 ┃
┃    Result: SupplierRepository, SupplierDTO                        ┃
┃                                                                   ┃
┃    MATCH (caller)-[:CALLS]->(c:Class {name: "SupplierService"})  ┃
┃    RETURN caller.name;                                            ┃
┃    Result: SupplierController (REST API)                          ┃
┃                                                                   ┃
┃    Decision: Impact = Controller + Service + DTO                  ┃
┃                                                                   ┃
┃ 3️⃣ Vector Embeddings:                                            ┃
┃    Query: "email validation method implementation java"           ┃
┃    Result: EmailValidator.java, UserValidator.java               ┃
┃                                                                   ┃
┃ 4️⃣ RAG Documents:                                                ┃
┃    Query: "project structure service layer patterns"              ┃
┃    Result: Services go in src/services/                           ┃
┃            Tests go in src/tests/                                 ┃
┃            Use @Service annotation                                ┃
┃                                                                   ┃
┃ 5️⃣ File System Check:                                            ┃
┃    Path("src/tests/SupplierServiceTest.java").exists()           ┃
┃    Result: FALSE                                                  ┃
┃    Decision: Need to CREATE test file                             ┃
┃                                                                   ┃
┃ LLM Call:                                                         ┃
┃   Prompt: "Create architectural plan"                             ┃
┃   Context:                                                        ┃
┃     - Existing file structure (SQLite)                            ┃
┃     - Dependencies (Neo4j)                                        ┃
┃     - Similar implementations (Vector)                            ┃
┃     - Project patterns (RAG)                                      ┃
┃                                                                   ┃
┃ Output:                                                           ┃
┃   tasks: [                                                        ┃
┃     {                                                             ┃
┃       id: "task-1",                                               ┃
┃       title: "Add validateEmail method",                          ┃
┃       file_path: "src/services/SupplierService.java",            ┃
┃       task_type: MODIFY,  ← From SQLite check                    ┃
┃       language: JAVA,                                             ┃
┃       dependencies: [],                                           ┃
┃       complexity: 2                                               ┃
┃     },                                                            ┃
┃     {                                                             ┃
┃       id: "task-2",                                               ┃
┃       title: "Create validation tests",                           ┃
┃       file_path: "src/tests/SupplierServiceTest.java",           ┃
┃       task_type: CREATE,  ← From file system check               ┃
┃       language: JAVA,                                             ┃
┃       dependencies: ["task-1"],                                   ┃
┃       complexity: 2                                               ┃
┃     }                                                             ┃
┃   ]                                                               ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                                 ↓
                    ┌────────────┴────────────┐
                    ↓                         ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓   ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ AGENT 4: RAG FOR TESTS     ┃   ┃ AGENT 5: RAG FOR CODE      ┃
┃ (PARALLEL BRANCH A)        ┃   ┃ (PARALLEL BRANCH B)        ┃
┃ File: workflow.py:344-376  ┃   ┃ File: workflow.py:378-410  ┃
┃                            ┃   ┃                            ┃
┃ Knowledge Used:            ┃   ┃ Knowledge Used:            ┃
┃                            ┃   ┃                            ┃
┃ ✅ Vector Embeddings       ┃   ┃ ✅ Vector Embeddings       ┃
┃    Focus: test_behavior    ┃   ┃    Focus: architecture     ┃
┃    Query:                  ┃   ┃    Query:                  ┃
┃    "email validation tests"┃   ┃    "email validation code" ┃
┃                            ┃   ┃                            ┃
┃ ✅ RAG Documents           ┃   ┃ ✅ RAG Documents           ┃
┃    Types: ["tests",        ┃   ┃    Types: ["source_code", ┃
┃            "business_rules"]┃   ┃            "patterns"]     ┃
┃                            ┃   ┃                            ┃
┃ ✅ File System             ┃   ┃ ✅ SQLite AST              ┃
┃    Read existing tests     ┃   ┃    Get class structure     ┃
┃                            ┃   ┃                            ┃
┃ Result:                    ┃   ┃ ✅ File System (CRITICAL)  ┃
┃   test_rag_context: [      ┃   ┃    Read EXISTING file:     ┃
┃     {                      ┃   ┃    "SupplierService.java"  ┃
┃       file: "EmailValidator┃   ┃                            ┃
┃              Test.java",   ┃   ┃ Result:                    ┃
┃       content: "@Test      ┃   ┃   code_rag_context: [      ┃
┃         shouldAcceptValid" ┃   ┃     {                      ┃
┃     },                     ┃   ┃       file: "EmailValidator┃
┃     {                      ┃   ┃              .java",       ┃
┃       file: "UserValidator ┃   ┃       content: "Pattern.  ┃
┃              Test.java",   ┃   ┃         compile(regex)"    ┃
┃       content: "test edge  ┃   ┃     },                     ┃
┃         cases"             ┃   ┃     {                      ┃
┃     }                      ┃   ┃       file: "SupplierServic┃
┃   ]                        ┃   ┃              e.java",      ┃
┃                            ┃   ┃       content: "public clas┃
┃                            ┃   ┃         SupplierService { "┃
┃                            ┃   ┃         // EXISTING CODE   ┃
┃                            ┃   ┃     }                      ┃
┃                            ┃   ┃   ]                        ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛   ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                    ↓                         ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓   ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ AGENT 6: TEST GENERATOR    ┃   ┃ AGENT 7: CODE GENERATOR    ┃
┃ File: test_generator.py    ┃   ┃ File: code_generator.py    ┃
┃                            ┃   ┃                            ┃
┃ Knowledge Used:            ┃   ┃ Knowledge Used:            ┃
┃   ✅ test_rag_context      ┃   ┃   ✅ code_rag_context      ┃
┃      (from Agent 4)        ┃   ┃      (from Agent 5)        ┃
┃                            ┃   ┃   ✅ existing_file_content ┃
┃ NO direct queries!         ┃   ┃      (from Agent 5)        ┃
┃                            ┃   ┃                            ┃
┃ LLM Call:                  ┃   ┃ NO direct queries!         ┃
┃   Prompt:                  ┃   ┃                            ┃
┃   "Generate JUnit tests"   ┃   ┃ LLM Call:                  ┃
┃   Context:                 ┃   ┃   Prompt:                  ┃
┃   - Test patterns from RAG ┃   ┃   "Modify this file"       ┃
┃   - Business requirements  ┃   ┃   Context:                 ┃
┃   - Edge cases             ┃   ┃   - Existing file content  ┃
┃                            ┃   ┃   - Code patterns from RAG ┃
┃ Output:                    ┃   ┃   - Requirements           ┃
┃   SupplierServiceTest.java ┃   ┃                            ┃
┃   (NEW FILE)               ┃   ┃ Output:                    ┃
┃                            ┃   ┃   SupplierService.java     ┃
┃   @Test                    ┃   ┃   (MODIFIED FILE)          ┃
┃   shouldValidateEmail() {  ┃   ┃                            ┃
┃     // test valid email    ┃   ┃   public class Supplier    ┃
┃   }                        ┃   ┃     Service {              ┃
┃                            ┃   ┃     // EXISTING CODE       ┃
┃   @Test                    ┃   ┃                            ┃
┃   shouldRejectInvalidEmail ┃   ┃     // NEW METHOD          ┃
┃   () {                     ┃   ┃     private void validate  ┃
┃     // test invalid        ┃   ┃       Email(String) {      ┃
┃   }                        ┃   ┃       // validation logic  ┃
┃                            ┃   ┃     }                      ┃
┃   @Test                    ┃   ┃   }                        ┃
┃   shouldRejectNullEmail()  ┃   ┃                            ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛   ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                    └────────────┬────────────┘
                                 ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ AGENT 8: BUILD EXECUTOR                                           ┃
┃ File: build_executor.py                                           ┃
┃                                                                   ┃
┃ Knowledge Used:                                                   ┃
┃   ✅ File System ONLY                                             ┃
┃      Read generated files to compile                              ┃
┃                                                                   ┃
┃ Action:                                                           ┃
┃   $ mvn clean compile                                             ┃
┃                                                                   ┃
┃ Output:                                                           ┃
┃   build_status: SUCCESS                                           ┃
┃   compilation_errors: []                                          ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                                 ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ AGENT 9: TEST EXECUTOR                                            ┃
┃ File: test_executor.py                                            ┃
┃                                                                   ┃
┃ Knowledge Used:                                                   ┃
┃   ✅ File System ONLY                                             ┃
┃      Read test files to execute                                   ┃
┃                                                                   ┃
┃ Action:                                                           ┃
┃   $ mvn test                                                      ┃
┃                                                                   ┃
┃ Output:                                                           ┃
┃   test_status: SUCCESS                                            ┃
┃   tests_passed: 3                                                 ┃
┃   tests_failed: 0                                                 ┃
┃   test_failures: []                                               ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                                 ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                       ✅ WORKFLOW COMPLETE                        ┃
┃                                                                   ┃
┃ Generated Files:                                                  ┃
┃   ✅ src/services/SupplierService.java (MODIFIED)                ┃
┃   ✅ src/tests/SupplierServiceTest.java (CREATED)                ┃
┃                                                                   ┃
┃ Build Status: ✅ SUCCESS                                          ┃
┃ Test Status: ✅ 3/3 PASSED                                        ┃
┃                                                                   ┃
┃ Code Changes:                                                     ┃
┃   + Added validateEmail() method                                  ┃
┃   + Added EMAIL_PATTERN constant                                  ┃
┃   + Modified saveSupplier() to call validation                    ┃
┃                                                                   ┃
┃ Total Time: 45 seconds                                            ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

---

## 🔑 KEY INSIGHTS FROM FLOW

### **1. Knowledge Propagation**
```
Investigation → Analysis → Planning
   (broad)      (focused)   (comprehensive)

Planning → RAG Agents
           (targeted queries)

RAG Agents → Generators
             (NO more queries, use retrieved context)
```

### **2. Planning Agent is the Knowledge Hub**
- **Receives**: Requirements from Analysis
- **Queries**: ALL knowledge layers (SQLite, Neo4j, Vector, RAG, Files)
- **Decides**: CREATE vs MODIFY, task breakdown, dependencies
- **Outputs**: Comprehensive architectural plan
- **Impact**: Determines success of entire workflow

### **3. RAG Separation (TDD)**
```
Agent 4 (RAG Tests)         Agent 5 (RAG Code)
      ↓                            ↓
  Behavior                   Architecture
      ↓                            ↓
Agent 6 (Test Gen)          Agent 7 (Code Gen)
      ↓                            ↓
   INDEPENDENT GENERATION
```
No knowledge leak between test and code!

### **4. File System Usage Pattern**
- **Planning**: Light (check existence)
- **RAG for Code**: Heavy (read existing content)
- **Code Generator**: Heavy (use content from RAG)
- **Executors**: Light (just run commands)

### **5. SQLite + Neo4j Critical Moments**
```
Planning Agent queries:
  ├─ SQLite: "Does SupplierService.java exist?"
  │          → YES → task_type = MODIFY
  │          → NO  → task_type = CREATE
  │
  └─ Neo4j:  "What depends on SupplierService?"
             → SupplierController
             → Impact analysis complete
```

---

## 📊 KNOWLEDGE FLOW SUMMARY

| Phase | Agent | SQLite | Neo4j | Vector | RAG | Files |
|-------|-------|--------|-------|--------|-----|-------|
| **Understanding** | 1-2 | - | - | Heavy | Heavy | - |
| **Planning** | 3 | **Critical** | **Critical** | Heavy | Heavy | Light |
| **Retrieval** | 4-5 | Light | Light | **Heavy** | **Heavy** | **Critical** |
| **Generation** | 6-7 | - | - | - | (passed) | (passed) |
| **Execution** | 8-9 | - | - | - | - | Light |
| **Fixing** | 10 | Light | - | Light | Focused | Light |

---

**This is your enterprise-grade autonomous engineering architecture! 🚀**
