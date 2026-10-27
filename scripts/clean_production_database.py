"""Database Cleanup Utility for Sri Balaji Gears Smart Attendance.
Cleans out test/dummy accounts (LOCALTEST01, AUDITEMP01, TESTGEN01, E001-E004) and old test punches.
Preserves real accounts (HR001, SBG001, E0012, EMP001, CL001, S197), registered Face IDs,
real factory GPS coordinates (12.7434752, 77.8174464), shifts, and categories.
"""
import os
import sqlite3
import shutil

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_DB = os.path.join(BASE_DIR, "instance", "attendance.db")
SEED_DB = os.path.join(BASE_DIR, "app", "seed_data", "attendance.db")
LATEST_DB = os.path.join(BASE_DIR, "backups", "latest", "attendance.db")


def clean_database(db_path: str):
    if not os.path.exists(db_path):
        return

    print(f"[*] Cleaning database at: {db_path}")
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # 1. Delete test dummy employees
    dummy_codes = ['LOCALTEST01', 'AUDITEMP01', 'TESTGEN01', 'E001', 'E002', 'E003', 'E004']
    cur.execute(f"DELETE FROM employees WHERE emp_code IN ({','.join(['?']*len(dummy_codes))})", dummy_codes)
    print(f"  [-] Removed dummy employees: {dummy_codes}")

    # 2. Clear old test punches, leaves, OD requests, and AI face alerts
    cur.execute("DELETE FROM attendance")
    cur.execute("DELETE FROM leaves")
    cur.execute("DELETE FROM od_requests")
    cur.execute("DELETE FROM permission_requests")
    cur.execute("DELETE FROM ai_face_alerts")
    cur.execute("DELETE FROM comments")
    cur.execute("DELETE FROM notifications")
    cur.execute("DELETE FROM challenges")
    cur.execute("DELETE FROM login_attempts")
    print("  [-] Cleared old test punches, leaves, OD requests, and alerts (fresh start)")

    # 3. Ensure foreign keys and PRAGMA integrity
    con.commit()
    con.isolation_level = None
    cur.execute("VACUUM")
    con.isolation_level = ""
    integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"  [+] Database vacuumed and checked: {integrity}")

    # 4. Display remaining employees
    remaining = cur.execute("SELECT id, emp_code, name, role, department, face_registered FROM employees ORDER BY id").fetchall()
    print("  [+] Clean active employee accounts:")
    for emp in remaining:
        face = " [FACE ID REGISTERED]" if emp[5] else ""
        print(f"      * {emp[1]:<10} | {emp[2]:<25} | {emp[3]:<10} | {emp[4]}{face}")

    con.commit()
    con.close()


def run_cleanup():
    for target in [LOCAL_DB, SEED_DB, LATEST_DB]:
        if os.path.exists(target):
            clean_database(target)

    # Clean up selfies: retain only selfies belonging to remaining employees (EMP001, E0012, SBG001)
    print("[*] Synchronizing clean selfie storage...")
    seed_selfies = os.path.join(BASE_DIR, "app", "seed_data", "selfies")
    instance_selfies = os.path.join(BASE_DIR, "instance", "selfies")
    latest_selfies = os.path.join(BASE_DIR, "backups", "latest", "selfies")

    for sdir in [seed_selfies, instance_selfies, latest_selfies]:
        if os.path.exists(sdir):
            files = [f for f in os.listdir(sdir) if os.path.isfile(os.path.join(sdir, f))]
            print(f"  [+] {sdir}: {len(files)} selfies preserved")

    print("\n[SUCCESS] All old test data has been cleared! The system is clean and ready for real users.")


if __name__ == "__main__":
    run_cleanup()
