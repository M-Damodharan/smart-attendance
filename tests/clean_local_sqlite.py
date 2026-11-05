import sqlite3
import os

for p in ["app/seed_data/attendance.db", "instance/attendance.db"]:
    if os.path.exists(p):
        con = sqlite3.connect(p)
        con.execute("DELETE FROM employees WHERE emp_code IN ('AUDITEMP01', 'LOCALTEST01', 'TESTGEN01', 'TEST001', 'TEST002')")
        con.execute("DELETE FROM attendance WHERE emp_id NOT IN (SELECT id FROM employees)")
        con.execute("DELETE FROM audit_log WHERE entity_id NOT IN (SELECT id FROM employees) AND entity='employee'")
        con.commit()
        rows = [r[0] for r in con.execute("SELECT emp_code FROM employees ORDER BY emp_code").fetchall()]
        con.close()
        print(p, "clean employees:", rows)
