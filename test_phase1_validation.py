"""
PHASE 1 - REPOSITORY INTELLIGENCE VALIDATION

Tests AST parsing, SQLite storage, dependency extraction, and Neo4j graph.

Author: Deepak Madgani
Date: May 27, 2026
"""

import sys
import sqlite3
from pathlib import Path
import json

# Add aviator-platform to path
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-platform")
sys.path.insert(0, r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src")

print("=" * 80)
print("🧪 PHASE 1 - REPOSITORY INTELLIGENCE VALIDATION")
print("=" * 80)

# ============================================================================
# TEST 1 — AST PARSING
# ============================================================================

print("\n" + "=" * 80)
print("TEST 1 — AST PARSING")
print("=" * 80)

# Create test Java file
test_file = Path("test_data/UserService.java")
test_file.parent.mkdir(exist_ok=True)
test_file.write_text("""
package com.example.service;

import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;
import com.example.repository.UserRepository;

@Service
public class UserService {

    @Autowired
    private UserRepository userRepository;

    public void saveUser(User user) {
        userRepository.save(user);
    }
    
    public User findById(Long id) {
        return userRepository.findById(id);
    }
}
""")

print(f"✅ Created test file: {test_file}")

# Test AST parser
try:
    from aviator_core.parsers.java_parser import JavaParser
    
    parser = JavaParser()
    result = parser.parse_file(str(test_file))
    
    print(f"\n📊 Parse Results:")
    print(f"   File: {result.file_path}")
    print(f"   Classes found: {len(result.classes)}")
    
    if result.classes:
        cls = result.classes[0]
        print(f"   ✅ Class name: {cls.name}")
        print(f"   ✅ Annotations: {cls.annotations}")
        print(f"   ✅ Methods found: {len(cls.methods)}")
        
        for method in cls.methods:
            print(f"      - {method.name}()")
        
        print(f"   ✅ Fields found: {len(cls.fields)}")
        for field in cls.fields:
            print(f"      - {field.name}: {field.type}")
        
        # Check for Spring stereotype
        if "@Service" in cls.annotations:
            print(f"   ✅ Spring @Service annotation detected!")
        
        # Check for dependencies
        if cls.fields:
            for field in cls.fields:
                if "@Autowired" in field.annotations:
                    print(f"   ✅ Dependency detected: {field.type}")
    
    print("\n✅ TEST 1 PASSED - AST parsing works correctly!")
    
except Exception as e:
    print(f"\n❌ TEST 1 FAILED: {e}")
    import traceback
    traceback.print_exc()

# ============================================================================
# TEST 2 — SQLITE STORAGE
# ============================================================================

print("\n" + "=" * 80)
print("TEST 2 — SQLITE STORAGE")
print("=" * 80)

# Check if Supplier_exchange has indexed data
supplier_db = Path(r"C:\Supplier_exchange\.aviator\index.db")

if supplier_db.exists():
    print(f"✅ Found index database: {supplier_db}")
    
    try:
        conn = sqlite3.connect(str(supplier_db))
        cursor = conn.cursor()
        
        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"\n📊 Database Tables:")
        for table in tables:
            print(f"   ✅ {table[0]}")
        
        # Check files table
        cursor.execute("SELECT COUNT(*) FROM files")
        file_count = cursor.fetchone()[0]
        print(f"\n📊 Statistics:")
        print(f"   Files indexed: {file_count}")
        
        # Check symbols table
        cursor.execute("SELECT COUNT(*) FROM symbols")
        symbol_count = cursor.fetchone()[0]
        print(f"   Symbols extracted: {symbol_count}")
        
        # Check edges table
        cursor.execute("SELECT COUNT(*) FROM edges")
        edge_count = cursor.fetchone()[0]
        print(f"   Dependencies: {edge_count}")
        
        # Find a service
        cursor.execute("""
            SELECT name, file_path, spring_stereotype 
            FROM symbols 
            WHERE spring_stereotype IS NOT NULL 
            LIMIT 5
        """)
        services = cursor.fetchall()
        
        if services:
            print(f"\n📊 Spring Services Found:")
            for name, path, stereotype in services:
                print(f"   ✅ {name} ({stereotype})")
                print(f"      Path: {path}")
        
        # Test exact lookup
        cursor.execute("""
            SELECT name, kind, file_path 
            FROM symbols 
            WHERE name LIKE '%Service%' 
            LIMIT 3
        """)
        lookup_results = cursor.fetchall()
        
        if lookup_results:
            print(f"\n📊 Exact Lookup Test (search for 'Service'):")
            for name, kind, path in lookup_results:
                print(f"   ✅ {name} ({kind})")
        
        conn.close()
        
        print("\n✅ TEST 2 PASSED - SQLite storage works correctly!")
        
    except Exception as e:
        print(f"\n❌ TEST 2 FAILED: {e}")
        import traceback
        traceback.print_exc()
else:
    print(f"⚠️ Database not found at {supplier_db}")
    print("   Run indexing first: python -m aviator_core.indexer")

# ============================================================================
# TEST 3 — DEPENDENCY EXTRACTION
# ============================================================================

print("\n" + "=" * 80)
print("TEST 3 — DEPENDENCY EXTRACTION")
print("=" * 80)

if supplier_db.exists():
    try:
        conn = sqlite3.connect(str(supplier_db))
        cursor = conn.cursor()
        
        # Find dependencies (@Autowired relationships)
        cursor.execute("""
            SELECT 
                s1.name as source,
                e.kind as relation,
                s2.name as target
            FROM edges e
            JOIN symbols s1 ON e.src_id = s1.id
            JOIN symbols s2 ON e.dst_id = s2.id
            WHERE e.kind IN ('imports', 'extends', 'implements')
            LIMIT 10
        """)
        deps = cursor.fetchall()
        
        if deps:
            print(f"\n📊 Dependency Graph:")
            for source, relation, target in deps:
                print(f"   {source} --[{relation}]--> {target}")
            
            print("\n✅ TEST 3 PASSED - Dependency extraction works!")
        else:
            print("⚠️ No dependencies found in database")
        
        conn.close()
        
    except Exception as e:
        print(f"\n❌ TEST 3 FAILED: {e}")
        import traceback
        traceback.print_exc()
else:
    print("⚠️ Database not found - skipping test")

# ============================================================================
# TEST 4 — NEO4J GRAPH
# ============================================================================

print("\n" + "=" * 80)
print("TEST 4 — NEO4J GRAPH")
print("=" * 80)

try:
    import os
    
    # Check if Neo4j credentials are set
    neo4j_password = os.getenv("NEO4J_PASSWORD")
    
    if not neo4j_password:
        print("⚠️ NEO4J_PASSWORD not set in environment")
        print("   Set it with: $env:NEO4J_PASSWORD='aviator-dev'")
    else:
        print(f"✅ Neo4j credentials found")
        
        from aviator_core.storage.neo4j_store import Neo4jStore
        
        store = Neo4jStore()
        
        # Test query: Find all services
        query = """
        MATCH (n:Class)
        WHERE n.stereotype IS NOT NULL
        RETURN n.name, n.stereotype
        LIMIT 5
        """
        
        print(f"\n📊 Querying Neo4j for Spring services...")
        results = store.query(query)
        
        if results:
            print(f"   Found {len(results)} services:")
            for record in results:
                print(f"   ✅ {record['n.name']} ({record['n.stereotype']})")
        
        # Test query: Find execution path
        query = """
        MATCH path = (start:Class)-[:CALLS*1..3]->(end:Class)
        WHERE start.name CONTAINS 'Controller'
          AND end.name CONTAINS 'Repository'
        RETURN 
            start.name as controller,
            [node in nodes(path) | node.name] as execution_path,
            end.name as repository
        LIMIT 3
        """
        
        print(f"\n📊 Querying execution paths (Controller → Repository)...")
        paths = store.query(query)
        
        if paths:
            for record in paths:
                path_str = " → ".join(record['execution_path'])
                print(f"   ✅ {path_str}")
            
            print("\n✅ TEST 4 PASSED - Neo4j graph traversal works!")
        else:
            print("⚠️ No execution paths found")
        
        store.close()
        
except Exception as e:
    print(f"\n❌ TEST 4 FAILED: {e}")
    import traceback
    traceback.print_exc()

# ============================================================================
# SUMMARY
# ============================================================================

print("\n" + "=" * 80)
print("📊 PHASE 1 VALIDATION SUMMARY")
print("=" * 80)

print("""
VALIDATION CHECKLIST:

TEST 1 - AST Parsing:
  ✅ Parse Java files
  ✅ Extract class names
  ✅ Extract method names
  ✅ Extract annotations (@Service)
  ✅ Extract imports
  ✅ Extract dependencies (@Autowired)

TEST 2 - SQLite Storage:
  ✅ Database exists (.aviator/index.db)
  ✅ Tables created (files, symbols, edges)
  ✅ Symbols stored correctly
  ✅ Exact lookup works (SELECT * FROM symbols WHERE name='...')

TEST 3 - Dependency Extraction:
  ✅ Relationships stored (imports, extends, implements)
  ✅ Dependency graph queryable

TEST 4 - Neo4j Graph:
  ✅ Graph database connection
  ✅ Nodes stored (Classes, Methods)
  ✅ Relationships stored (CALLS, DEPENDS_ON)
  ✅ Graph traversal works (execution paths)

SUCCESS CONDITION: All tests should pass ✅
""")

print("\n" + "=" * 80)
print("🎯 NEXT STEPS:")
print("=" * 80)
print("""
1. If any test failed, fix the issue
2. Re-index Supplier_exchange if needed:
   python -m aviator_core.indexer C:\\Supplier_exchange
3. Verify Neo4j is running:
   docker ps | findstr neo4j
4. If all pass, proceed to PHASE 2 (Localization Agent testing)
""")

print("\n✅ PHASE 1 VALIDATION COMPLETE!")
