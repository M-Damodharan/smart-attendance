import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

con = sqlite3.connect("instance/attendance.db")
con.row_factory = sqlite3.Row

# 1. Update HR Admin with password damo12345
hr_pw = generate_password_hash("damo12345")
con.execute("UPDATE employees SET password_hash=?, must_change_pw=0, active=1 WHERE emp_code='HR001'", (hr_pw,))

# 2. Update employee EMP001 with password 123456789
emp_pw = generate_password_hash("123456789")
con.execute("UPDATE employees SET emp_code='EMP001', name='Employee Staff', email='staff@sribalajigears.com', password_hash=?, must_change_pw=0, active=1 WHERE id=2", (emp_pw,))

# 3. Remove demo employees (id 3, 4, 5) and associated records
con.execute("DELETE FROM leaves WHERE emp_id IN (3, 4, 5)")
con.execute("DELETE FROM od_requests WHERE emp_id IN (3, 4, 5)")
con.execute("DELETE FROM attendance WHERE emp_id IN (3, 4, 5)")
con.execute("DELETE FROM employees WHERE id IN (3, 4, 5)")

# 4. Clear all login attempt lockouts
con.execute("DELETE FROM login_attempts")

con.commit()

print("--- Updated Database Accounts ---")
for e in con.execute("SELECT id, emp_code, name, role, active FROM employees").fetchall():
    print(dict(e))

hr = con.execute("SELECT password_hash FROM employees WHERE emp_code='HR001'").fetchone()
print("\nHR001 check 123456789:", check_password_hash(hr["password_hash"], "123456789"))

emp = con.execute("SELECT password_hash FROM employees WHERE emp_code='EMP001'").fetchone()
print("EMP001 check 123456789:", check_password_hash(emp["password_hash"], "123456789"))

con.close()
