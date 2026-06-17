import sqlite3

db = sqlite3.connect(r'C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\.aviator_projects.db')
rows = db.execute("SELECT id, name, status FROM projects WHERE status='indexing'").fetchall()
print('Stuck projects:', rows)
db.execute("UPDATE projects SET status='error', indexed=0 WHERE status='indexing'")
db.commit()
print('Reset done')
db.close()
