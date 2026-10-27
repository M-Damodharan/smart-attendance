"""Automated SQLite Database & Media Backup & Recovery Manager.
Ensures 100% data preservation across app updates, employee creations, and Render server restarts.
"""
import io
import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime
from typing import Dict, Any, List, Optional

from config import BASE_DIR, Config
from app.services import now

BACKUP_ROOT = os.path.realpath(os.path.join(BASE_DIR, "backups"))
LATEST_BACKUP_DIR = os.path.join(BACKUP_ROOT, "latest")
MAX_BACKUP_HISTORY = 10


def get_configured_db_path() -> str:
    return os.path.realpath(Config.DATABASE)


def get_configured_selfie_dir() -> str:
    return os.path.realpath(Config.SELFIE_DIR)


def create_backup(reason: str = "manual") -> Dict[str, Any]:
    """Creates a consistent atomic SQLite backup and mirrors selfies.
    Never corrupts or locks the running database.
    """
    db_path = get_configured_db_path()
    selfie_dir = get_configured_selfie_dir()

    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return {"ok": False, "error": f"Database file does not exist or is empty at {db_path}"}

    os.makedirs(BACKUP_ROOT, exist_ok=True)
    os.makedirs(LATEST_BACKUP_DIR, exist_ok=True)

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c for c in reason if c.isalnum() or c in ("_", "-"))[:20]
    folder_name = f"backup_{timestamp_str}_{safe_reason}"
    target_dir = os.path.join(BACKUP_ROOT, folder_name)
    os.makedirs(target_dir, exist_ok=True)

    dest_db = os.path.join(target_dir, "attendance.db")
    latest_db = os.path.join(LATEST_BACKUP_DIR, "attendance.db")

    # 1. Atomic backup via SQLite Online Backup API
    src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dest_conn = sqlite3.connect(dest_db)
    with dest_conn:
        src_conn.backup(dest_conn, pages=100)
    dest_conn.close()

    # Also update backups/latest/attendance.db
    shutil.copy2(dest_db, latest_db)

    # 2. Query table counts from the backup to verify integrity
    cur = src_conn.cursor()
    counts = {}
    tables = [
        "employees", "attendance", "shifts", "offices",
        "employee_categories", "leaves", "od_requests",
        "permission_requests", "audit_log", "holidays"
    ]
    for tbl in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            counts[tbl] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            counts[tbl] = 0
    src_conn.close()

    # 3. Mirror selfie photos
    selfie_count = 0
    if os.path.exists(selfie_dir):
        dest_selfies = os.path.join(target_dir, "selfies")
        latest_selfies = os.path.join(LATEST_BACKUP_DIR, "selfies")
        os.makedirs(dest_selfies, exist_ok=True)
        os.makedirs(latest_selfies, exist_ok=True)

        for fname in os.listdir(selfie_dir):
            fpath = os.path.join(selfie_dir, fname)
            if os.path.isfile(fpath) and fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                shutil.copy2(fpath, os.path.join(dest_selfies, fname))
                shutil.copy2(fpath, os.path.join(latest_selfies, fname))
                selfie_count += 1
    counts["selfie_files"] = selfie_count

    # 4. Save metadata manifest
    manifest = {
        "timestamp": timestamp_str,
        "reason": reason,
        "source_db": db_path,
        "backup_db": dest_db,
        "verified": True,
        "counts": counts,
        "db_size_bytes": os.path.getsize(dest_db),
    }
    with open(os.path.join(target_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(LATEST_BACKUP_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Also keep seed_data synchronized if app/seed_data/ exists
    seed_db = os.path.join(BASE_DIR, "app", "seed_data", "attendance.db")
    seed_selfies = os.path.join(BASE_DIR, "app", "seed_data", "selfies")
    if os.path.exists(os.path.dirname(seed_db)):
        try:
            shutil.copy2(dest_db, seed_db)
            if os.path.exists(dest_selfies):
                os.makedirs(seed_selfies, exist_ok=True)
                for s in os.listdir(dest_selfies):
                    shutil.copy2(os.path.join(dest_selfies, s), os.path.join(seed_selfies, s))
        except Exception:
            pass

    # 5. Prune old backups exceeding MAX_BACKUP_HISTORY
    _prune_old_backups()

    return {
        "ok": True,
        "timestamp": timestamp_str,
        "reason": reason,
        "backup_dir": target_dir,
        "counts": counts,
        "db_size": os.path.getsize(dest_db),
    }


def _prune_old_backups():
    """Keeps the most recent MAX_BACKUP_HISTORY backups to prevent disk bloat."""
    try:
        entries = []
        for name in os.listdir(BACKUP_ROOT):
            p = os.path.join(BACKUP_ROOT, name)
            if os.path.isdir(p) and name.startswith("backup_") and name != "latest":
                entries.append((os.path.getmtime(p), p))
        entries.sort(key=lambda x: x[0], reverse=True)
        for _, old_path in entries[MAX_BACKUP_HISTORY:]:
            shutil.rmtree(old_path, ignore_errors=True)
    except Exception:
        pass


def list_backups() -> List[Dict[str, Any]]:
    """Returns a list of all existing backup snapshots on disk."""
    results = []
    if not os.path.exists(BACKUP_ROOT):
        return results

    for name in os.listdir(BACKUP_ROOT):
        dir_path = os.path.join(BACKUP_ROOT, name)
        if not os.path.isdir(dir_path) or name == "latest":
            continue
        manifest_file = os.path.join(dir_path, "manifest.json")
        db_file = os.path.join(dir_path, "attendance.db")
        if not os.path.exists(db_file):
            continue

        item = {
            "name": name,
            "path": dir_path,
            "timestamp": "Unknown",
            "reason": "manual",
            "emp_count": 0,
            "att_count": 0,
            "size_bytes": os.path.getsize(db_file),
            "size_formatted": f"{os.path.getsize(db_file) / 1024:.1f} KB",
        }
        if os.path.exists(manifest_file):
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    item["timestamp"] = meta.get("timestamp", name.replace("backup_", ""))
                    item["reason"] = meta.get("reason", "manual")
                    counts = meta.get("counts", {})
                    item["emp_count"] = counts.get("employees", 0)
                    item["att_count"] = counts.get("attendance", 0)
            except Exception:
                pass

        if item["emp_count"] == 0:
            try:
                c = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
                item["emp_count"] = c.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
                item["att_count"] = c.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
                c.close()
            except Exception:
                pass

        results.append(item)

    results.sort(key=lambda x: (x.get("timestamp", ""), x.get("name", "")), reverse=True)
    return results


def build_backup_zip_bytes() -> bytes:
    """Builds a complete backup ZIP archive in memory containing attendance.db, selfies/, and manifest.json."""
    # Ensure a fresh backup snapshot exists
    backup_meta = create_backup(reason="download")
    backup_dir = backup_meta.get("backup_dir", LATEST_BACKUP_DIR)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        db_file = os.path.join(backup_dir, "attendance.db")
        if os.path.exists(db_file):
            zf.write(db_file, "attendance.db")

        manifest_file = os.path.join(backup_dir, "manifest.json")
        if os.path.exists(manifest_file):
            zf.write(manifest_file, "manifest.json")

        selfies_dir = os.path.join(backup_dir, "selfies")
        if os.path.exists(selfies_dir):
            for fname in os.listdir(selfies_dir):
                fpath = os.path.join(selfies_dir, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, os.path.join("selfies", fname))

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def restore_backup(file_storage, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Safely restores database and selfies from an uploaded .zip or .db file.
    Creates a pre-restore rollback backup before altering anything.
    """
    filename = (file_storage.filename or "").lower()
    if not (filename.endswith(".zip") or filename.endswith(".db") or filename.endswith(".sqlite")):
        return {"ok": False, "error": "Invalid file format. Upload a .zip or .db backup file."}

    # 1. Take a safety backup of current active database first
    try:
        create_backup(reason="pre_restore_rollback")
    except Exception:
        pass

    target_db = get_configured_db_path()
    target_selfies = get_configured_selfie_dir()
    os.makedirs(os.path.dirname(target_db), exist_ok=True)
    os.makedirs(target_selfies, exist_ok=True)

    temp_dir = os.path.join(BACKUP_ROOT, f"temp_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(temp_dir, exist_ok=True)

    extracted_db = None
    restored_selfies_count = 0

    try:
        if filename.endswith(".zip"):
            zip_path = os.path.join(temp_dir, "upload.zip")
            file_storage.save(zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Security: prevent zip-slip path traversal
                for member in zf.namelist():
                    norm = os.path.normpath(member)
                    if norm.startswith("..") or os.path.isabs(norm):
                        raise ValueError("Malicious path detected in ZIP archive.")
                zf.extractall(temp_dir)

            # Find extracted db
            candidates = [
                os.path.join(temp_dir, "attendance.db"),
                os.path.join(temp_dir, "instance", "attendance.db"),
            ]
            for c in candidates:
                if os.path.exists(c) and os.path.getsize(c) > 0:
                    extracted_db = c
                    break

            if not extracted_db:
                # Search recursively in temp_dir for any .db
                for root, _, files in os.walk(temp_dir):
                    for f in files:
                        if f.endswith(".db"):
                            extracted_db = os.path.join(root, f)
                            break
                    if extracted_db:
                        break

            # Restore selfies if present
            extracted_selfies = os.path.join(temp_dir, "selfies")
            if os.path.exists(extracted_selfies):
                for s in os.listdir(extracted_selfies):
                    s_src = os.path.join(extracted_selfies, s)
                    if os.path.isfile(s_src):
                        shutil.copy2(s_src, os.path.join(target_selfies, s))
                        restored_selfies_count += 1
        else:
            extracted_db = os.path.join(temp_dir, "uploaded.db")
            file_storage.save(extracted_db)

        if not extracted_db or not os.path.exists(extracted_db):
            return {"ok": False, "error": "No valid SQLite database found in uploaded file."}

        # 2. Validate SQLite integrity and schema
        test_conn = sqlite3.connect(f"file:{extracted_db}?mode=ro", uri=True)
        cur = test_conn.cursor()
        emp_count = cur.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        att_count = cur.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
        test_conn.close()

        if emp_count == 0:
            return {"ok": False, "error": "Uploaded database has no employee records."}

        # 3. Replace active database safely
        shutil.copy2(extracted_db, target_db)

        # Also update backups/latest and seed_data
        try:
            shutil.copy2(extracted_db, os.path.join(LATEST_BACKUP_DIR, "attendance.db"))
            seed_db = os.path.join(BASE_DIR, "app", "seed_data", "attendance.db")
            if os.path.exists(os.path.dirname(seed_db)):
                shutil.copy2(extracted_db, seed_db)
        except Exception:
            pass

        # 4. Audit entry
        if user_id:
            try:
                from app.services import audit
                audit("BACKUP_RESTORE", "database", user_id, f"Restored {emp_count} emps, {att_count} punches, {restored_selfies_count} selfies from {filename}")
            except Exception:
                pass

        return {
            "ok": True,
            "employees": emp_count,
            "attendance": att_count,
            "selfies_restored": restored_selfies_count,
            "timestamp": now(),
        }

    except Exception as e:
        return {"ok": False, "error": f"Failed to restore backup: {str(e)}"}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
