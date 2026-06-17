import sqlite3, json

db = sqlite3.connect(r'C:\CC4E\.aviator\index.db')

# 1. Index stats
nfiles = db.execute('SELECT COUNT(*) FROM files').fetchone()[0]
nsyms  = db.execute('SELECT COUNT(*) FROM symbols').fetchone()[0]
nedges = db.execute('SELECT COUNT(*) FROM edges').fetchone()[0]
print(f'Index: {nfiles} files, {nsyms} symbols, {nedges} edges')
print()

# 2. jheader annotations
rows = db.execute(
    "SELECT path, annotations FROM symbols WHERE path LIKE '%jheader%' AND annotations IS NOT NULL"
).fetchall()
print('--- jheader annotations ---')
for p, a in rows:
    print(f'  {p}')
    print(f'    -> {a}')
print()

# 3. jdashboard annotations
rows = db.execute(
    "SELECT path, annotations FROM symbols WHERE path LIKE '%jdashboard%' AND annotations IS NOT NULL"
).fetchall()
print('--- jdashboard annotations ---')
for p, a in rows:
    print(f'  {p}')
    print(f'    -> {a}')
print()

# 4. seltok count
n_seltok = db.execute("SELECT COUNT(*) FROM symbols WHERE annotations LIKE '%seltok%'").fetchone()[0]
print(f'Symbols with seltok annotations: {n_seltok}')
print()

# 5. Language distribution
rows = db.execute(
    "SELECT language, COUNT(*) as n FROM files GROUP BY language ORDER BY n DESC"
).fetchall()
print('--- Language distribution ---')
for lang, n in rows:
    print(f'  {lang}: {n}')
print()

# 6. GenericControllerHandler path
rows = db.execute(
    "SELECT path FROM files WHERE path LIKE '%GenericController%'"
).fetchall()
print('--- GenericControllerHandler files ---')
for (p,) in rows:
    print(f'  {p}')

db.close()
