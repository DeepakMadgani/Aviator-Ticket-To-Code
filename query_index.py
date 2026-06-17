"""Query the indexed database to show what was extracted"""
import sqlite3

db_path = r'C:\Supplier_exchange\.aviator\index.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 80)
print("📊 INDEXING RESULTS - REAL DATA FROM YOUR CODEBASE")
print("=" * 80)

# Overall stats
print("\n✅ DATABASE STATISTICS:")
print(f"   Total Files:   {cursor.execute('SELECT COUNT(*) FROM files').fetchone()[0]:,}")
print(f"   Total Symbols: {cursor.execute('SELECT COUNT(*) FROM symbols').fetchone()[0]:,}")
print(f"   Total Edges:   {cursor.execute('SELECT COUNT(*) FROM edges').fetchone()[0]:,}")

# Symbol breakdown
print("\n📦 SYMBOL TYPES:")
symbol_counts = cursor.execute('''
    SELECT kind, COUNT(*) as count 
    FROM symbols 
    GROUP BY kind 
    ORDER BY count DESC
''').fetchall()
for kind, count in symbol_counts:
    print(f"   {kind:20} {count:,}")

# Spring stereotypes
print("\n🌱 SPRING FRAMEWORK COMPONENTS:")
spring_counts = cursor.execute('''
    SELECT spring_stereotype, COUNT(*) as count 
    FROM symbols 
    WHERE spring_stereotype IS NOT NULL 
    GROUP BY spring_stereotype
''').fetchall()
if spring_counts:
    for stereotype, count in spring_counts:
        print(f"   {stereotype:20} {count:,}")
else:
    print("   (No Spring stereotypes detected)")

# Sample classes
print("\n📚 SAMPLE CLASSES (First 10):")
classes = cursor.execute('''
    SELECT name, qualified_name, path 
    FROM symbols 
    WHERE kind='class' 
    LIMIT 10
''').fetchall()
for name, qname, fpath in classes:
    fname = fpath.split('\\')[-1] if fpath else 'unknown'
    print(f"   {name:30} from {fname}")

# Sample methods
print("\n🔧 SAMPLE METHODS (First 10):")
methods = cursor.execute('''
    SELECT name, qualified_name, path
    FROM symbols
    WHERE kind='method' 
    LIMIT 10
''').fetchall()
for name, qname, fpath in methods:
    fname = fpath.split('\\')[-1] if fpath else 'unknown'
    print(f"   {name:30} in {fname}")

# Relationships
print("\n🔗 RELATIONSHIP TYPES:")
edge_counts = cursor.execute('''
    SELECT kind, COUNT(*) as count 
    FROM edges 
    GROUP BY kind 
    ORDER BY count DESC
''').fetchall()
for kind, count in edge_counts:
    print(f"   {kind:20} {count:,}")

# Sample inheritance relationships
print("\n👨‍👩‍👦 SAMPLE INHERITANCE (First 5):")
extends = cursor.execute('''
    SELECT s1.name as child, s2.name as parent
    FROM edges e
    JOIN symbols s1 ON e.src_id = s1.id
    JOIN symbols s2 ON e.dst_id = s2.id
    WHERE e.kind = 'extends'
    LIMIT 5
''').fetchall()
if extends:
    for child, parent in extends:
        print(f"   {child:30} extends {parent}")
else:
    print("   (No extends relationships found)")

print("\n" + "=" * 80)
print("✅ This is REAL parsed data from your 1336 Java files!")
print("   You can now use this for intelligent code navigation and generation.")
print("=" * 80)

conn.close()
