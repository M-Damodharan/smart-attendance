import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.turso_db import connect_turso
import config

c = connect_turso(config.Config.TURSO_DATABASE_URL, config.Config.TURSO_AUTH_TOKEN)

print("Before cleanup in Turso:")
print("Employees:", [r["emp_code"] for r in c.execute("SELECT emp_code FROM employees").fetchall()])

# Delete any test accounts
c.execute("DELETE FROM employees WHERE emp_code IN ('AUDITEMP01', 'LOCALTEST01', 'TESTGEN01', 'TEST001', 'TEST002')")
c.execute("DELETE FROM attendance WHERE emp_id NOT IN (SELECT id FROM employees)")
c.execute("DELETE FROM audit_log WHERE entity_id NOT IN (SELECT id FROM employees) AND entity='employee'")

# Verify remaining employees
print("\nAfter cleanup in Turso:")
remaining = c.execute("SELECT id, emp_code, name, department, role, category_id, active FROM employees ORDER BY emp_code").fetchall()
for emp in remaining:
    print(f"  [{emp['emp_code']}] {emp['name']} | Dept: {emp['department']} | Role: {emp['role']} | Cat: {emp['category_id']}")

print(f"\nTotal clean valid production employees: {len(remaining)}")
