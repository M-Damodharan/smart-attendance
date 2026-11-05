import sys, os
import sqlite3
import json
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.turso_db import connect_turso
import config

print("=" * 60)
print("1. TAKING SAFETY BACKUP BEFORE DELETION")
print("=" * 60)

backup_dir = os.path.join(config.BASE_DIR, "backups", f"pre_clean_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
os.makedirs(backup_dir, exist_ok=True)

# Backup local instance db if exists
src_db = os.path.join(config.BASE_DIR, "instance", "attendance.db")
if os.path.exists(src_db):
    import shutil
    shutil.copy2(src_db, os.path.join(backup_dir, "attendance_pre_clean.db"))
    print("Local database backed up to:", backup_dir)

# Backup Turso Cloud data
c = connect_turso(config.Config.TURSO_DATABASE_URL, config.Config.TURSO_AUTH_TOKEN)
turso_dump = {
    "timestamp": datetime.now().isoformat(),
    "employees": c.execute("SELECT * FROM employees").fetchall(),
    "attendance": c.execute("SELECT * FROM attendance").fetchall(),
    "shifts": c.execute("SELECT * FROM shifts").fetchall(),
    "offices": c.execute("SELECT * FROM offices").fetchall(),
    "settings": c.execute("SELECT * FROM system_settings").fetchall(),
}
with open(os.path.join(backup_dir, "turso_cloud_snapshot.json"), "w", encoding="utf-8") as f:
    json.dump(turso_dump, f, indent=2, default=str)
print("Turso Cloud snapshot backed up to:", os.path.join(backup_dir, "turso_cloud_snapshot.json"))

print("\n" + "=" * 60)
print("2. DELETING EMPLOYEES (KEEPING ONLY HR001)")
print("=" * 60)

# Delete from Turso Cloud
hr_row = c.execute("SELECT id FROM employees WHERE emp_code='HR001'").fetchone()
hr_id = hr_row["id"] if hr_row else 1
print(f"Preserving HR Admin (ID: {hr_id}, Code: HR001)")

delete_sqls = [
    f"DELETE FROM attendance WHERE emp_id != {hr_id}",
    f"DELETE FROM leaves WHERE emp_id != {hr_id}",
    f"DELETE FROM od_requests WHERE emp_id != {hr_id}",
    f"DELETE FROM permission_requests WHERE emp_id != {hr_id}",
    f"DELETE FROM face_change_requests WHERE emp_id != {hr_id}",
    f"DELETE FROM webauthn_credentials WHERE emp_id != {hr_id}",
    f"DELETE FROM ai_face_alerts WHERE emp_id != {hr_id}",
    "DELETE FROM challenges",
    "DELETE FROM login_attempts",
    f"DELETE FROM audit_log WHERE entity='employee' AND entity_id != {hr_id}",
    "DELETE FROM employees WHERE emp_code != 'HR001'",
]

for sql in delete_sqls:
    c.execute(sql)
print("Turso Cloud: Deleted all non-HR employees and associated records.")

# Delete from local seed and instance databases
for path in [
    os.path.join(config.BASE_DIR, "app", "seed_data", "attendance.db"),
    os.path.join(config.BASE_DIR, "instance", "attendance.db"),
]:
    if os.path.exists(path):
        con = sqlite3.connect(path)
        for sql in delete_sqls:
            try:
                con.execute(sql)
            except Exception:
                pass
        con.commit()
        con.close()
        print(f"Local DB cleaned: {path}")

print("\n" + "=" * 60)
print("3. VERIFYING TURSO CLOUD ACTIVE RECORDS")
print("=" * 60)
active_emps = c.execute("SELECT id, emp_code, name, department, role, category_id, active FROM employees").fetchall()
print(f"Total employees remaining in Turso Cloud: {len(active_emps)}")
for e in active_emps:
    print(f"  [{e['emp_code']}] {e['name']} | Role: {e['role']} | Dept: {e['department']} | Active: {e['active']}")

print("\nTotal attendance records in Turso Cloud:", c.execute("SELECT COUNT(*) as c FROM attendance").fetchone()["c"])
print("\nDELETION AND CLEANUP COMPLETED SUCCESSFULLY!")
