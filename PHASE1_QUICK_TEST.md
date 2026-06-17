# ✅ PHASE 1 VALIDATION - QUICK TEST GUIDE

## 🎯 **WHAT WE FIXED**

1. ✅ **Neo4j password reset** - Now using `aviator-dev`
2. ✅ **Backend Neo4j integration** - Now properly populates Neo4j from SQLite
3. ✅ **Corrected SQL queries** - Using `path` instead of `file_path`

---

## 📋 **TEST SEQUENCE**

### **STEP 1: Set Neo4j Password**

```powershell
$env:NEO4J_PASSWORD = "aviator-dev"
```

Run this in your PowerShell terminal (the one where backend runs).

---

### **STEP 2: Restart Backend**

```powershell
# Stop if running (Ctrl+C)
# Then restart:
cd C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample
python backend_clean.py
```

Look for this line:
```
✅ Neo4j enabled: True
```

---

### **STEP 3: Re-Index Supplier_exchange**

Open http://localhost:3000 in browser and click:
1. "Index Project" button for Supplier_exchange
2. Wait for indexing to complete (watch terminal logs)

**Expected logs:**
```
🔍 Starting indexing for Supplier_exchange...
✅ Indexed Supplier_exchange: X files, Y symbols
🌐 Indexing to Neo4j...
   Found Z symbols
   ✅ Inserted Z symbols to Neo4j
   Found W edges
   ✅ Inserted W edges to Neo4j
✅ Neo4j indexing complete
```

---

### **STEP 4: Test SQLite (CORRECTED QUERY)**

Open `C:\Supplier_exchange\.aviator\index.db` in DB Browser.

**Query 1 - Find Spring Services:**
```sql
SELECT name, kind, spring_stereotype, path 
FROM symbols 
WHERE spring_stereotype IS NOT NULL 
LIMIT 10;
```

**Expected**: List of Java classes with annotations like `@Service`, `@Controller`, `@Repository`

**Query 2 - Find All Classes:**
```sql
SELECT name, kind, path 
FROM symbols 
WHERE kind = 'class'
LIMIT 10;
```

**Query 3 - Find Dependencies:**
```sql
SELECT 
    s1.name as source,
    e.kind as relation,
    s2.name as target
FROM edges e
JOIN symbols s1 ON e.src_id = s1.id
JOIN symbols s2 ON e.dst_id = s2.id
LIMIT 10;
```

---

### **STEP 5: Test Neo4j**

1. Open http://localhost:7474
2. Login: `neo4j` / `aviator-dev`
3. Run these queries:

**Query 1 - Count Nodes:**
```cypher
MATCH (n) RETURN count(n) as total_nodes;
```

**Expected**: Should be > 0 (not 0 anymore!)

**Query 2 - Find Spring Services:**
```cypher
MATCH (s:Symbol)
WHERE s.spring_stereotype IS NOT NULL
RETURN s.name, s.spring_stereotype
LIMIT 10;
```

**Query 3 - Find Dependencies:**
```cypher
MATCH (source)-[r]->(target)
RETURN source.name, type(r), target.name
LIMIT 10;
```

**Query 4 - Find Execution Paths:**
```cypher
MATCH path = (start:Symbol)-[:CALLS*1..3]->(end:Symbol)
WHERE start.spring_stereotype = '@Controller'
  AND end.spring_stereotype = '@Repository'
RETURN 
    start.name as controller,
    [node in nodes(path) | node.name] as path,
    end.name as repository
LIMIT 5;
```

---

## ✅ **SUCCESS CRITERIA**

| Test | What to Check | Expected Result |
|------|---------------|-----------------|
| **Backend Logs** | Neo4j enabled | `Neo4j enabled: True` |
| **Indexing Logs** | Symbols inserted | `✅ Inserted X symbols to Neo4j` |
| **SQLite Query 1** | Spring services found | > 0 rows |
| **SQLite Query 2** | Classes found | > 0 rows  |
| **SQLite Query 3** | Dependencies found | > 0 rows |
| **Neo4j Query 1** | Total nodes | > 0 (not 0!) |
| **Neo4j Query 2** | Spring services | > 0 rows |
| **Neo4j Query 3** | Relationships | > 0 rows |

---

## ⚠️ **IF TESTS FAIL**

### **Neo4j shows 0 nodes:**
- Check backend logs for errors during indexing
- Make sure `NEO4J_PASSWORD=aviator-dev` is set
- Try re-indexing the project

### **SQLite queries return 0 rows:**
- Project might not be Java/Spring
- Check if files exist at `C:\Supplier_exchange\area-service\src\main\java\`
- Try searching for `.java` files manually

### **No @Service/@Controller/@Repository found:**
- This project might use different framework
- Try searching for `@Component` or just `class`

---

## 🎯 **WHAT'S NEXT**

After all tests pass:
- **PHASE 2**: Test Localization Agent (finds exact files using AST)
- **PHASE 3**: Test AST Patch Engine (surgical modifications)
- **PHASE 4**: Test end-to-end workflow (ticket → code)

---

**RUN THESE TESTS NOW!** 🚀
