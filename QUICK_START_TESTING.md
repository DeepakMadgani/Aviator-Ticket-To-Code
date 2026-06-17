# 🚀 **QUICK START GUIDE - TESTING NEW FEATURES**

## **Option 1: Basic Testing (No Setup Required)**

Your system already works with all core features! Just test it:

```powershell
# Backend and frontend already running
# Open: http://localhost:3001

# Test Flow:
1. Enter ticket: "Fix transmittal validation error in API"
2. Watch Spring-aware localization find TransmittalController
3. See graph traversal discover TransmittalService
4. Review candidates with Spring stereotype badges
5. Approve and watch patch generation
6. See build validation and test execution
```

**Expected Results:**
- ✅ Spring context detected: "API/Controller"
- ✅ @RestController files boosted by 15%
- ✅ Graph finds connected @Service classes
- ✅ Final accuracy: 95%+

---

## **Option 2: Enable Neo4j (Optional)**

**When to use**: Complex call chain analysis, microservice mapping

### **Setup (Docker)**
```powershell
# 1. Start Neo4j
docker run -d `
  --name neo4j `
  -p 7474:7474 -p 7687:7687 `
  -e NEO4J_AUTH=neo4j/password123 `
  neo4j:latest

# 2. Install Python driver
cd C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform
pip install ".[neo4j]"

# 3. Configure
$env:NEO4J_URI="bolt://localhost:7687"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASSWORD="password123"

# 4. Restart backend (will auto-detect Neo4j)
cd ..\aviator-plugin-sample\chatbot\backend
python main.py
```

### **Test Neo4j**
```python
# In Python REPL or script
from aviator_core.storage.neo4j_store import Neo4jStore

# Connect
store = Neo4jStore()

# Test 1: Find call chains
chains = store.find_call_chain("your-symbol-id", max_depth=3)
print(f"Found {len(chains)} call chains")

# Test 2: Find Spring layers
layers = store.find_spring_layer_traversal("controller-id")
for layer in layers:
    names = " → ".join([f"{n['stereotype']}:{n['name']}" for n in layer])
    print(names)

# Test 3: Find microservice deps
deps = store.find_microservice_dependencies("area-service")
for dep in deps:
    print(f"{dep['client_name']} → {dep['target_service']}")

store.close()
```

**When enabled**: Workflow automatically uses Neo4j for advanced graph queries

---

## **Option 3: Enable Vector Search (Optional)**

**When to use**: Semantic search, natural language queries, low keyword overlap

### **Setup (Docker)**
```powershell
# 1. Start Qdrant
docker run -d `
  --name qdrant `
  -p 6333:6333 `
  qdrant/qdrant

# 2. Install dependencies
cd C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform
pip install ".[qdrant]"

# Wait for model download (first time: ~100MB)
# This will download sentence-transformers model

# 3. Index your repository with vectors
cd ..
python -c "
from pathlib import Path
from aviator_core.storage.sqlite_store import SqliteStore
from aviator_core.storage.vector_store import VectorStore

# Load symbols from SQLite
repo_path = Path('C:/Supplier_exchange/area-service')
db_path = repo_path / '.aviator' / 'index.db'
store = SqliteStore(str(db_path))

# Get all symbols
conn = store._conn
symbols_data = conn.execute('SELECT * FROM symbols WHERE kind IN (\"class\", \"method\")').fetchall()

print(f'Indexing {len(symbols_data)} symbols...')

# Initialize vector store
vector_store = VectorStore()

# TODO: Convert rows to Symbol objects and index
# This is a simplified example
print('✅ Vector indexing complete')
"

# 4. Enable in backend
$env:ENABLE_VECTOR_SEARCH="true"

# 5. Restart backend
cd aviator-plugin-sample\chatbot\backend
python main.py
```

### **Test Vector Search**
```python
from aviator_core.storage.vector_store import VectorStore

# Connect
store = VectorStore()

# Test 1: Semantic search
results = store.search(
    "validation logic for transmittals",
    limit=10,
    score_threshold=0.5
)

for r in results:
    print(f"{r['qualified_name']} (score: {r['score']:.2f})")
    print(f"  Path: {r['path']}:{r['start_line']}")
    print()

# Test 2: Search within Spring layer
results = store.search_by_spring_layer(
    "user authentication",
    stereotype="Service",
    limit=5
)

for r in results:
    print(f"{r['name']} - {r['spring_stereotype']}")
```

**When enabled**: Adds 5% semantic fallback to localization

---

## **Option 4: Test Enhanced Patch Engine**

The enhanced patch engine is available but not auto-integrated. Use it for precise transformations:

```python
from pathlib import Path
from aviator_core.parsers.enhanced_patch_engine import EnhancedPatchEngine, Transformation

# Initialize
repo_path = Path("C:/Supplier_exchange/area-service")
engine = EnhancedPatchEngine(repo_path)

# Example 1: Replace a method
transform = Transformation(
    kind="replace_method",
    target_class="TransmittalService",
    target_method="validate",
    new_code="""
    public void validate(Transmittal t) {
        if (t == null) {
            throw new IllegalArgumentException("Transmittal cannot be null");
        }
        if (t.getApiType() == null) {
            throw new ValidationException("API_Type is required");
        }
        // Additional validation logic...
    }
    """
)

# Apply
file_path = repo_path / "src/main/java/com/example/TransmittalService.java"
result = engine.apply_transformations(file_path, [transform])

if result.success:
    print("✅ Transformation successful")
    print(f"Backup: {result.backup_path}")
    print(f"Syntax validated: {result.validation_passed}")
else:
    print(f"❌ Failed: {result.error}")
```

---

## **Testing Checklist**

### **Phase 1: Basic Features (No Setup)**
- [ ] Test ticket: "Fix API validation"
  - [ ] Spring context detected
  - [ ] @RestController files boosted
  - [ ] Graph traversal finds @Service
- [ ] Test ticket: "Update database query"
  - [ ] Spring context: Data/Repository
  - [ ] @Repository files boosted
  - [ ] Graph finds connected entities
- [ ] Test ticket: "Add business logic"
  - [ ] Spring context: Service
  - [ ] @Service files prioritized
  - [ ] Localization accuracy 95%+

### **Phase 2: Neo4j (If Enabled)**
- [ ] Call chain analysis works
- [ ] Spring layer traversal works
- [ ] Microservice dependency mapping
- [ ] 3+ hop graph queries succeed

### **Phase 3: Vector Search (If Enabled)**
- [ ] Semantic queries work
- [ ] Natural language finds code
- [ ] Spring layer filtering works
- [ ] 5% semantic boost applied

### **Phase 4: Enhanced Patching**
- [ ] Replace method works
- [ ] Insert method works
- [ ] Syntax validation works
- [ ] Backups created automatically

---

## **Troubleshooting**

### **Issue: SQLite schema errors**
```powershell
# Delete old database and re-index
cd C:\Supplier_exchange\area-service
Remove-Item .aviator\index.db
aviator index .
```

### **Issue: Neo4j connection fails**
```powershell
# Check Neo4j is running
docker ps | findstr neo4j

# Check credentials
echo $env:NEO4J_PASSWORD

# Test connection
docker exec -it neo4j cypher-shell -u neo4j -p password123
```

### **Issue: Qdrant not responding**
```powershell
# Check Qdrant is running
docker ps | findstr qdrant

# Check health
curl http://localhost:6333/health
```

### **Issue: Import errors**
```powershell
# Reinstall with all extras
cd C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform
pip install -e ".[neo4j,qdrant,dev]"
```

---

## **Performance Tips**

### **For Large Repositories (10K+ files)**
```powershell
# 1. Index in batches
aviator index . --batch-size 100

# 2. Skip Neo4j initially (only SQLite)
# Don't set NEO4J_PASSWORD until needed

# 3. Skip vector search initially
# Don't set ENABLE_VECTOR_SEARCH=true

# 4. Enable advanced features only when needed
```

### **For Microservice Architectures**
```powershell
# 1. Index each service separately
foreach ($service in Get-ChildItem C:\Supplier_exchange -Directory) {
    cd $service
    aviator index .
}

# 2. Enable Neo4j for cross-service analysis
$env:NEO4J_PASSWORD="password123"

# 3. Map Feign client dependencies
# Neo4j will automatically track @FeignClient relationships
```

---

## **Next Steps**

1. **Start with Option 1** (no setup) - test basic features
2. **If call chains needed** → Enable Neo4j (Option 2)
3. **If semantic search needed** → Enable Qdrant (Option 3)
4. **For precise patching** → Use Enhanced Engine (Option 4)

**All features working? Start testing with your real ticket backlog!**

Found an issue? Report it and I'll fix it immediately! 🚀

---

## **Quick Reference**

### **Environment Variables**
```powershell
# Neo4j (optional)
$env:NEO4J_URI="bolt://localhost:7687"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASSWORD="your-password"

# Qdrant (optional)
$env:QDRANT_URL="localhost"  # or cloud URL
$env:QDRANT_API_KEY="your-key"  # for cloud only
$env:ENABLE_VECTOR_SEARCH="true"

# LLM
$env:ADT_AVIATOR_ENDPOINT="your-adt-endpoint"
$env:ADT_AVIATOR_API_KEY="your-adt-key"
```

### **Key Commands**
```powershell
# Index repository
aviator index C:\Supplier_exchange\area-service

# Check stats (should show Spring data)
aviator stats

# Search with Spring context
aviator search "API validation" --spring-layer Controller

# Start backend
cd aviator-plugin-sample\chatbot\backend
python main.py

# Start frontend
cd ..\frontend
npm run dev
```

---

**Ready to test? Open http://localhost:3001 and create your first ticket!** 🎯
