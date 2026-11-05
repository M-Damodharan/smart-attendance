import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.turso_db import connect_turso
import config
from werkzeug.security import check_password_hash

c = connect_turso(config.Config.TURSO_DATABASE_URL, config.Config.TURSO_AUTH_TOKEN)
hr = c.execute("SELECT emp_code, password_hash, role, active, category_id FROM employees WHERE emp_code='HR001'").fetchone()
print("HR in Turso:", hr["emp_code"], "Role:", hr["role"], "Active:", hr["active"])
print("damo12345 match:", check_password_hash(hr["password_hash"], "damo12345"))

for emp in c.execute("SELECT emp_code, password_hash FROM employees").fetchall():
    match_damo = check_password_hash(emp["password_hash"], "damo12345")
    match_123 = check_password_hash(emp["password_hash"], "123456789")
    print(f"[{emp['emp_code']}] damo12345: {match_damo} | 123456789: {match_123}")
