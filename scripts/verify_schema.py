import sqlite3
import os

con = sqlite3.connect("instance/attendance.db")
cur = con.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables in DB:", len(tables))
for t in ["sunday_working", "late_coming_requests", "employee_salary_profiles", "monthly_payroll", "payroll_audit_log"]:
    assert t in tables, f"Missing table {t}"
    count = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"Table '{t}': EXISTS (rows: {count})")

# Check salary profiles
prof_count = cur.execute("SELECT COUNT(*) FROM employee_salary_profiles").fetchone()[0]
emp_count = cur.execute("SELECT COUNT(*) FROM employees WHERE active=1").fetchone()[0]
print(f"Active employees: {emp_count}, Seeded salary profiles: {prof_count}")
con.close()
print("SCHEMA VERIFICATION COMPLETE: ALL 5 TABLES ACTIVE!")
