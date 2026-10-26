"""Consistent SQLite Database & Storage Backup Utility for Sri Balaji Gears Smart Attendance."""
import os
import shutil
import sqlite3
import json
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.environ.get("DATABASE", os.path.join(INSTANCE_DIR, "attendance.db"))
SELFIE_DIR = os.environ.get("SELFIE_DIR", os.path.join(INSTANCE_DIR, "selfies"))
BACKUP_ROOT = os.path.join(BASE_DIR, "backups")


def create_backup():
    if not os.path.exists(DB_PATH):
        print(f"[!] Database file does not exist at {DB_PATH}")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = os.path.join(BACKUP_ROOT, f"backup_{timestamp}")
    os.makedirs(target_dir, exist_ok=True)

    dest_db = os.path.join(target_dir, "attendance.db")
    print(f"[*] Creating atomic SQLite backup: {DB_PATH} -> {dest_db}")

    # Use SQLite online backup API for ACID consistency
    src_conn = sqlite3.connect(DB_PATH)
    dest_conn = sqlite3.connect(dest_db)
    with dest_conn:
        src_conn.backup(dest_conn, pages=100)
    dest_conn.close()

    # Query source counts
    cur = src_conn.cursor()
    counts = {}
    tables = [
        "employees", "attendance", "leaves", "od_requests",
        "permission_requests", "face_change_requests",
        "shifts", "offices", "employee_categories", "audit_log"
    ]
    for tbl in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            counts[tbl] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            counts[tbl] = 0
    src_conn.close()

    # Backup selfies
    selfie_count = 0
    if os.path.exists(SELFIE_DIR):
        dest_selfies = os.path.join(target_dir, "selfies")
        os.makedirs(dest_selfies, exist_ok=True)
        for fname in os.listdir(SELFIE_DIR):
            fpath = os.path.join(SELFIE_DIR, fname)
            if os.path.isfile(fpath):
                shutil.copy2(fpath, os.path.join(dest_selfies, fname))
                selfie_count += 1
    counts["selfie_files"] = selfie_count

    # Verification: open target db and verify counts match
    v_conn = sqlite3.connect(f"file:{dest_db}?mode=ro", uri=True)
    v_cur = v_conn.cursor()
    verified_counts = {}
    for tbl in tables:
        try:
            v_cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            verified_counts[tbl] = v_cur.fetchone()[0]
        except sqlite3.OperationalError:
            verified_counts[tbl] = 0
    v_conn.close()

    for tbl in tables:
        if counts[tbl] != verified_counts[tbl]:
            raise ValueError(f"Backup mismatch for table {tbl}: expected {counts[tbl]}, got {verified_counts[tbl]}")

    manifest = {
        "timestamp": timestamp,
        "source_db": DB_PATH,
        "backup_db": dest_db,
        "verified": True,
        "counts": counts
    }

    manifest_path = os.path.join(target_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"[OK] Backup verified successfully at: {target_dir}")
    print(f"    Employees: {counts.get('employees', 0)}")
    print(f"    Attendance records: {counts.get('attendance', 0)}")
    print(f"    Leaves: {counts.get('leaves', 0)}")
    print(f"    OD Requests: {counts.get('od_requests', 0)}")
    print(f"    Permission Requests: {counts.get('permission_requests', 0)}")
    print(f"    Selfie Files: {selfie_count}")
    return target_dir


if __name__ == "__main__":
    create_backup()
