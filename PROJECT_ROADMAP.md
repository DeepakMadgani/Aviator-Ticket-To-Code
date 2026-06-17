# 🚀 Project Completion Roadmap - Aviator Autonomous Engineering Platform

## ✅ What's Already Complete

### Phase 1: Repository Intelligence Core (100% ✅)
- ✅ Tree-sitter Java parser (`aviator-platform/aviator_core/parsers/java_parser.py`)
- ✅ SQLite storage with FTS5 (`aviator-platform/aviator_core/storage/sqlite_store.py`)
- ✅ Symbol extraction (classes, methods, fields, relationships)
- ✅ Graph edge generation (calls, extends, implements)
- ✅ Indexer (`aviator-platform/aviator_core/indexer.py`)
- ✅ Query engine (`aviator-platform/aviator_core/query.py`)
- ✅ CLI tool (`aviator index`, `aviator search`, etc.)

### RAG Knowledge Base (100% ✅)
- ✅ Enterprise structure created (separate from codebase)
- ✅ Auto-generated from actual Supplier Exchange codebase
- ✅ 10 microservices documented
- ✅ 52 controllers, 179 services, 26 repositories extracted
- ✅ Architecture, business rules, API guides, workflows, troubleshooting

### Transparent Workflow UI (100% ✅)
- ✅ React frontend with workflow visualization
- ✅ FastAPI backend with workflow endpoints
- ✅ WebSocket real-time updates
- ✅ Workflow state management
- ✅ Human approval checkpoints UI

---

## 🎯 What Needs to Be Done to Run Tickets

### PHASE 2: Hybrid Localization Engine (CRITICAL - 0% ❌)

**Priority: HIGHEST** - This is the MOST IMPORTANT component!

#### 2.1 Implement Keyword Search (60% weight)
```python
# File: aviator-platform/aviator_core/localizer/keyword_search.py
class KeywordLocalizer:
    def search(self, ticket_description: str) -> List[FileCandidate]:
        # Extract keywords from ticket
        keywords = extract_keywords(ticket_description)
        
        # Search using SQLite FTS5
        results = sqlite_store.search_symbols(keywords)
        
        # Rank by relevance
        return rank_by_keyword_match(results, keywords)
```

**Tasks:**
- [ ] Create `aviator-platform/aviator_core/localizer/` directory
- [ ] Implement `keyword_search.py` with FTS5 queries
- [ ] Implement keyword extraction (regex + NLP)
- [ ] Add ranking algorithm (TF-IDF or similar)

#### 2.2 Implement Symbol Search (30% weight)
```python
# File: aviator-platform/aviator_core/localizer/symbol_search.py
class SymbolLocalizer:
    def search(self, entities: List[str]) -> List[FileCandidate]:
        # Search symbols table
        results = []
        for entity in entities:
            symbols = sqlite_store.query(
                "SELECT * FROM symbols WHERE name LIKE ?",
                f"%{entity}%"
            )
            results.extend(symbols)
        return results
```

**Tasks:**
- [ ] Create `symbol_search.py`
- [ ] Implement entity extraction from ticket
- [ ] Query symbols table with pattern matching
- [ ] Return method-level candidates (not just files)

#### 2.3 Implement Graph Traversal (5% weight)
```python
# File: aviator-platform/aviator_core/localizer/graph_search.py
class GraphLocalizer:
    def find_execution_path(self, entry_point: str) -> List[FileCandidate]:
        # Find controller → service → repository path
        return traverse_call_graph(entry_point)
```

**Tasks:**
- [ ] Create `graph_search.py`
- [ ] Implement graph traversal using edges table
- [ ] Find execution paths (Controller → Service → Repository)
- [ ] Calculate graph centrality scores

#### 2.4 Implement Hybrid Ranker (Combines all signals)
```python
# File: aviator-platform/aviator_core/localizer/hybrid_localizer.py
class HybridLocalizer:
    def localize(self, ticket: str) -> List[FileCandidate]:
        keyword_results = keyword_search.search(ticket)  # 60%
        symbol_results = symbol_search.search(ticket)     # 30%
        graph_results = graph_search.search(ticket)       # 5%
        vector_results = vector_search.search(ticket)     # 5% (optional)
        
        # Combine with weights
        return combine_and_rank(
            keyword_results * 0.6,
            symbol_results * 0.3,
            graph_results * 0.05,
            vector_results * 0.05
        )
```

**Tasks:**
- [ ] Create `hybrid_localizer.py`
- [ ] Implement weighted ranking algorithm
- [ ] Return top-N candidates with confidence scores
- [ ] Include method names and line ranges

---

### PHASE 3: Integration with Workflow Manager (30% 🔶)

#### 3.1 Connect Localization to Workflow
```python
# File: aviator-plugin-sample/chatbot/backend/workflow_manager.py

async def localize_files(self, workflow_id: str, repo_path: str):
    # Index repository if not already indexed
    if not is_indexed(repo_path):
        await index_repository(repo_path)
    
    # Run hybrid localization
    localizer = HybridLocalizer(repo_path)
    candidates = localizer.localize(
        ticket=workflow.ticket_description
    )
    
    # Store candidates in workflow state
    workflow.candidate_files = candidates
    
    await self.add_step(
        workflow_id,
        WorkflowPhase.LOCALIZATION,
        "completed",
        f"Found {len(candidates)} candidate files"
    )
```

**Tasks:**
- [ ] Import aviator-platform modules into workflow_manager
- [ ] Replace mock localization with real hybrid localizer
- [ ] Handle repository indexing on-demand
- [ ] Return real FileCandidate objects with scores

#### 3.2 Implement Classification (LLM)
**Current**: Uses simple keyword heuristics  
**Needed**: Call ADT Aviator LLM

```python
async def classify_operation(self, workflow_id: str, ticket: str):
    # Load domain context from RAG
    domain_docs = load_rag_docs(
        path=f"{repo_path}/../knowledge/supplier-exchange/domain",
        keywords=extract_entities(ticket)
    )
    
    # Call LLM
    prompt = f"""
    Classify this ticket:
    {ticket}
    
    Domain Context:
    {domain_docs}
    
    Return: code_modification, code_addition, bug_fix, or refactoring
    """
    
    classification = await adt_aviator_llm.classify(prompt)
    workflow.operation_type = classification
```

**Tasks:**
- [ ] Integrate ADT Aviator LLM client
- [ ] Implement RAG document loading
- [ ] Create classification prompt template
- [ ] Handle LLM responses

#### 3.3 Implement Impact Analysis
```python
async def analyze_impact(self, workflow_id: str, selected_files: List[str]):
    impact = ImpactAnalysis()
    
    for file_path in selected_files:
        # Find methods in file
        symbols = query_symbols_in_file(file_path)
        
        for symbol in symbols:
            # Find direct callers
            callers = find_callers(symbol.id)
            impact.direct_callers += len(callers)
            
            # Find transitive dependencies
            deps = traverse_dependencies(symbol.id)
            impact.transitive_dependents += len(deps)
            
            # Find affected tests
            tests = find_related_tests(symbol.name)
            impact.affected_tests += len(tests)
    
    # Calculate risk level
    impact.risk_level = calculate_risk(impact)
    
    return impact
```

**Tasks:**
- [ ] Implement caller analysis using graph edges
- [ ] Implement transitive dependency traversal
- [ ] Find test files related to changes
- [ ] Calculate risk metrics

---

### PHASE 4: LLM Integration for Patch Generation (0% ❌)

#### 4.1 Context Expansion
```python
def expand_context(localized_files: List[FileCandidate]) -> Dict:
    context = {
        "target_files": [],
        "dependencies": [],
        "architecture_rules": [],
        "coding_standards": [],
        "business_rules": []
    }
    
    for file_candidate in localized_files:
        # Load target method code
        method_code = extract_method_code(
            file_candidate.path,
            file_candidate.method_name,
            file_candidate.start_line,
            file_candidate.end_line
        )
        context["target_files"].append(method_code)
        
        # Load dependencies
        deps = load_dependencies(method_code)
        context["dependencies"].extend(deps)
    
    # Load relevant RAG docs
    context["architecture_rules"] = load_rag("architecture", keywords)
    context["coding_standards"] = load_rag("guidelines", keywords)
    context["business_rules"] = load_rag("business_rules", keywords)
    
    return context
```

**Tasks:**
- [ ] Implement method code extraction using tree-sitter
- [ ] Load dependency code
- [ ] Fetch relevant RAG documents
- [ ] Limit context size (LLM token limits)

#### 4.2 Patch Generation with LLM
```python
async def generate_patch(self, workflow_id: str, context: Dict):
    prompt = f"""
You are modifying Java code for Supplier Exchange.

# CODE TO MODIFY
{context['target_files']}

# DEPENDENCIES
{context['dependencies']}

# ARCHITECTURE RULES (MUST FOLLOW)
{context['architecture_rules']}

# CODING STANDARDS (MUST FOLLOW)
{context['coding_standards']}

# BUSINESS RULES (MUST PRESERVE)
{context['business_rules']}

# TICKET
{workflow.ticket_description}

Generate ONLY the modified method. Follow all rules above.
"""
    
    patch = await adt_aviator_llm.generate(prompt)
    return patch
```

**Tasks:**
- [ ] Create prompt template
- [ ] Integrate with ADT Aviator LLM
- [ ] Parse LLM response to extract code
- [ ] Validate generated code

#### 4.3 AST-Safe Patch Application
```python
def apply_patch(file_path: str, method_name: str, new_code: str):
    # Parse original file
    tree = parser.parse_file(file_path)
    
    # Find target method node
    method_node = find_method_node(tree, method_name)
    
    # Extract byte range
    start_byte = method_node.start_byte
    end_byte = method_node.end_byte
    
    # Replace only that method
    original_content = read_file(file_path)
    new_content = (
        original_content[:start_byte] +
        new_code +
        original_content[end_byte:]
    )
    
    # Validate syntax
    if not validate_syntax(new_content):
        raise SyntaxError("Generated code has syntax errors")
    
    # Write file
    write_file(file_path, new_content)
```

**Tasks:**
- [ ] Use tree-sitter to find method boundaries
- [ ] Replace only modified method (not whole file)
- [ ] Validate syntax after modification
- [ ] Handle formatting

---

### PHASE 5: Validation & Testing (0% ❌)

#### 5.1 Syntax Validation
```python
def validate_syntax(code: str) -> bool:
    try:
        tree = parser.parse(bytes(code, "utf-8"))
        return not tree.root_node.has_error
    except:
        return False
```

#### 5.2 Build Validation
```bash
# Run Maven build
cd {repo_path}
mvn clean compile
```

#### 5.3 Test Execution
```bash
# Run affected tests
mvn test -Dtest={affected_test_classes}
```

**Tasks:**
- [ ] Implement syntax validation
- [ ] Integrate with Maven build
- [ ] Run tests and capture results
- [ ] Parse test failures

---

### PHASE 6: Optional Enhancements (Later)

#### Vector Search (5% weight - optional)
- [ ] Set up Qdrant vector database
- [ ] Embed code and RAG documents
- [ ] Implement semantic search as fallback

#### Neo4j Graph Database (optional)
- [ ] Export SQLite graph to Neo4j
- [ ] Use for complex graph queries
- [ ] Visualize call graphs

---

## 📋 Step-by-Step Implementation Order

### Week 1-2: Core Localization
1. ✅ Index Supplier Exchange codebase
   ```bash
   aviator index C:\Supplier_exchange
   ```

2. ❌ Implement Keyword Search
   - Create keyword extractor
   - Query FTS5
   - Return ranked results

3. ❌ Implement Symbol Search
   - Query symbols table
   - Match against entities
   - Include method-level results

4. ❌ Implement Hybrid Ranker
   - Combine all signals
   - Weight appropriately
   - Return FileCandidate objects

5. ❌ Test localization manually
   ```python
   localizer = HybridLocalizer("C:\\Supplier_exchange")
   results = localizer.localize("Fix user registration validation")
   print(results)
   ```

### Week 3: LLM Integration
1. ❌ Get ADT Aviator LLM credentials
2. ❌ Implement LLM client
3. ❌ Create classification prompt
4. ❌ Create patch generation prompt
5. ❌ Test with simple examples

### Week 4: Workflow Integration
1. ❌ Connect localization to workflow_manager
2. ❌ Connect LLM to workflow_manager
3. ❌ Implement context expansion
4. ❌ Implement patch application
5. ❌ Test end-to-end with one simple ticket

### Week 5: Validation & Polish
1. ❌ Add syntax validation
2. ❌ Add build validation (Maven)
3. ❌ Add test execution
4. ❌ Improve error handling
5. ❌ Add logging and debugging

### Week 6: Production Readiness
1. ❌ Performance optimization
2. ❌ Error recovery
3. ❌ User documentation
4. ❌ Deployment setup

---

## 🚦 To Run Your First Ticket TODAY

### Minimum Viable Implementation (4-6 hours)

1. **Index the codebase** (Already works!)
   ```bash
   cd C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform
   python -m aviator_core.cli index C:\Supplier_exchange
   ```

2. **Simple Keyword Localizer** (2 hours)
   ```python
   # Quick implementation using existing search
   from aviator_core.query import find_symbols
   
   def simple_localize(ticket: str):
       keywords = ticket.lower().split()
       results = []
       for keyword in keywords:
           results.extend(find_symbols(keyword))
       return results[:5]  # Top 5
   ```

3. **Mock LLM for Testing** (1 hour)
   ```python
   # Use OpenAI/Claude API temporarily for testing
   import openai
   
   def generate_patch(context, ticket):
       response = openai.ChatCompletion.create(
           model="gpt-4",
           messages=[{"role": "user", "content": prompt}]
       )
       return response.choices[0].message.content
   ```

4. **Connect to Workflow** (1 hour)
   - Replace mock in workflow_manager.py
   - Test with UI

5. **Run First Ticket!** (30 min)
   - Start backend: `uvicorn main:app`
   - Start frontend: `npm run dev`
   - Enter ticket: "Fix null pointer in UserService"
   - Watch transparent workflow execute!

---

## 🎯 Priority Order

**CRITICAL PATH (Must Do First):**
1. Keyword Search (60% of localization)
2. Symbol Search (30% of localization)
3. Hybrid Ranker
4. LLM Integration
5. Patch Application

**Nice to Have (Later):**
- Graph traversal
- Vector search
- Neo4j integration
- Advanced validation

---

## 📚 Key Files to Create

```
aviator-platform/aviator_core/
├── localizer/
│   ├── __init__.py
│   ├── keyword_search.py        ❌ CREATE
│   ├── symbol_search.py         ❌ CREATE
│   ├── graph_search.py          ❌ CREATE (optional)
│   ├── hybrid_localizer.py      ❌ CREATE
│   └── file_candidate.py        ❌ CREATE
├── llm/
│   ├── __init__.py
│   ├── adt_client.py            ❌ CREATE
│   └── prompt_templates.py      ❌ CREATE
└── patcher/
    ├── __init__.py
    ├── context_expander.py      ❌ CREATE
    ├── patch_generator.py       ❌ CREATE
    └── patch_applicator.py      ❌ CREATE
```

---

## ✅ Success Criteria

You'll know the system works when:
1. ✅ Codebase indexed successfully
2. ✅ Localization returns relevant files (>70% accuracy)
3. ✅ LLM generates syntactically valid code
4. ✅ Patches apply without breaking build
5. ✅ Tests still pass after modification
6. ✅ UI shows transparent progress
7. ✅ Human can approve/reject at checkpoints

---

## 🎉 Ready to Start!

**Next Immediate Action:**
1. Index the Supplier Exchange codebase
2. Create `aviator-platform/aviator_core/localizer/keyword_search.py`
3. Implement simple keyword matching using FTS5

Want me to create the keyword_search.py file to get you started?
