"""Show Spring REST endpoints"""
import sqlite3
import json

conn = sqlite3.connect(r'C:\Supplier_exchange\.aviator\index.db')
cursor = conn.cursor()

print("\n🎯 SPRING REST CONTROLLERS WITH ENDPOINTS:\n")
controllers = cursor.execute("""
    SELECT name, spring_endpoints, path
    FROM symbols 
    WHERE spring_stereotype IN ('RestController', 'Controller') 
    AND spring_endpoints IS NOT NULL 
    LIMIT 5
""").fetchall()

for name, endpoints_json, fpath in controllers:
    if endpoints_json:
        endpoints = json.loads(endpoints_json)
        fname = fpath.split('\\')[-1] if fpath else 'unknown'
        print(f"   📦 {name} ({fname})")
        for endpoint in endpoints[:3]:
            print(f"      → {endpoint}")
        if len(endpoints) > 3:
            print(f"      ... and {len(endpoints)-3} more endpoints")
        print()

print("\n🔍 SAMPLE METHOD CALLS (proves call graph works):\n")
calls = cursor.execute("""
    SELECT s1.name as caller, s2.name as callee
    FROM edges e
    JOIN symbols s1 ON e.src_id = s1.id
    LEFT JOIN symbols s2 ON e.dst_id = s2.id
    WHERE e.kind = 'calls'
    AND s1.kind = 'method'
    LIMIT 10
""").fetchall()

for caller, callee in calls:
    if callee:
        print(f"   {caller:40} → calls → {callee}")
    else:
        print(f"   {caller:40} → calls → <external>")

conn.close()
