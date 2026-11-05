import sqlite3
import os

for db_path in ["app/seed_data/attendance.db", "instance/attendance.db"]:
    if os.path.exists(db_path):
        con = sqlite3.connect(db_path)
        con.execute("UPDATE employees SET category_id=1, department='IT', designation='Software Engineer' WHERE emp_code='SBG001'")
        con.commit()
        row = con.execute("SELECT emp_code, name, department, category_id FROM employees WHERE emp_code='SBG001'").fetchone()
        con.close()
        print(db_path, "->", row)
