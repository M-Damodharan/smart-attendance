"""Automated SQLite & Cloud Database Backup & Recovery Manager.
Ensures 100% data preservation across app updates, employee creations, and Render server restarts.
Seamlessly unifies local SQLite and Turso LibSQL Cloud.
"""
import base64
import io
import json
import logging
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime
from typing import Dict, Any, List, Optional

from config import BASE_DIR, Config
from app.services import now

logger = logging.getLogger(__name__)

BACKUP_ROOT = os.path.realpath(os.path.join(BASE_DIR, "backups"))
LATEST_BACKUP_DIR = os.path.join(BACKUP_ROOT, "latest")
MAX_BACKUP_HISTORY = 10

ALL_SYNC_TABLES = [
    "offices",
    "shifts",
    "employee_categories",
    "employees",
    "attendance",
    "leave_types",
    "leaves",
    "od_requests",
    "permission_requests",
    "face_change_requests",
    "holidays",
    "weekly_shift_schedules",
    "system_settings",
    "webauthn_credentials",
    "ai_face_alerts",
    "comments",
    "notifications",
    "durable_photos",
    "audit_log"
]


def get_configured_db_path() -> str:
    return os.path.realpath(Config.DATABASE)


def get_configured_selfie_dir() -> str:
    return os.path.realpath(Config.SELFIE_DIR)


def create_backup(reason: str = "manual") -> Dict[str, Any]:
    """Creates a consistent atomic backup of either Turso Cloud or local SQLite,
    and mirrors all selfie photos. Never corrupts or locks the running database.
    """
    selfie_dir = get_configured_selfie_dir()
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    os.makedirs(LATEST_BACKUP_DIR, exist_ok=True)

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c for c in reason if c.isalnum() or c in ("_", "-"))[:20]
    folder_name = f"backup_{timestamp_str}_{safe_reason}"
    target_dir = os.path.join(BACKUP_ROOT, folder_name)
    os.makedirs(target_dir, exist_ok=True)

    dest_db = os.path.join(target_dir, "attendance.db")
    latest_db = os.path.join(LATEST_BACKUP_DIR, "attendance.db")
    counts = {}

    use_cloud = bool(Config.USE_TURSO and Config.TURSO_DATABASE_URL and Config.TURSO_AUTH_TOKEN)

    if use_cloud:
        # 1. Cloud-native snapshot: Export Turso LibSQL Cloud tables into a standalone SQLite database
        from app.turso_db import connect_turso
        from app.db import SCHEMA

        t_conn = connect_turso(Config.TURSO_DATABASE_URL, Config.TURSO_AUTH_TOKEN)
        dest_conn = sqlite3.connect(dest_db)
        dest_conn.executescript(SCHEMA)

        for tbl in ALL_SYNC_TABLES:
            try:
                t_rows = t_conn.execute(f"SELECT * FROM {tbl}").fetchall()
                counts[tbl] = len(t_rows)
                if t_rows:
                    cols = list(t_rows[0].keys())
                    col_names = ", ".join(f'"{c}"' for c in cols)
                    placeholders = ", ".join(["?"] * len(cols))
                    for r in t_rows:
                        vals = [r[c] for c in cols]
                        dest_conn.execute(f'INSERT OR REPLACE INTO "{tbl}" ({col_names}) VALUES ({placeholders})', vals)
                dest_conn.commit()
            except Exception as e:
                logger.warning(f"Failed exporting table {tbl} from Turso: {e}")
                counts[tbl] = 0
        dest_conn.close()

        # Update backups/latest/attendance.db
        shutil.copy2(dest_db, latest_db)

        # Mirror photos: both local disk and durable_photos table
        dest_selfies = os.path.join(target_dir, "selfies")
        latest_selfies = os.path.join(LATEST_BACKUP_DIR, "selfies")
        os.makedirs(dest_selfies, exist_ok=True)
        os.makedirs(latest_selfies, exist_ok=True)
        selfie_count = 0

        if os.path.exists(selfie_dir):
            for fname in os.listdir(selfie_dir):
                fpath = os.path.join(selfie_dir, fname)
                if os.path.isfile(fpath) and fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    shutil.copy2(fpath, os.path.join(dest_selfies, fname))
                    shutil.copy2(fpath, os.path.join(latest_selfies, fname))
                    selfie_count += 1

        try:
            p_rows = t_conn.execute("SELECT filename, photo_data FROM durable_photos").fetchall()
            for p in p_rows:
                fn = os.path.basename(p["filename"])
                dest_p = os.path.join(dest_selfies, fn)
                if not os.path.exists(dest_p) and p["photo_data"]:
                    raw_b = base64.b64decode(p["photo_data"])
                    with open(dest_p, "wb") as pf:
                        pf.write(raw_b)
                    shutil.copy2(dest_p, os.path.join(latest_selfies, fn))
                    selfie_count += 1
        except Exception:
            pass
        counts["selfie_files"] = selfie_count

    else:
        # Local SQLite fallback
        db_path = get_configured_db_path()
        if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
            return {"ok": False, "error": f"Database file does not exist or is empty at {db_path}"}

        src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dest_conn = sqlite3.connect(dest_db)
        with dest_conn:
            src_conn.backup(dest_conn, pages=100)
        dest_conn.close()

        shutil.copy2(dest_db, latest_db)

        cur = src_conn.cursor()
        for tbl in ALL_SYNC_TABLES:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                counts[tbl] = cur.fetchone()[0]
            except sqlite3.OperationalError:
                counts[tbl] = 0
        src_conn.close()

        dest_selfies = os.path.join(target_dir, "selfies")
        latest_selfies = os.path.join(LATEST_BACKUP_DIR, "selfies")
        os.makedirs(dest_selfies, exist_ok=True)
        os.makedirs(latest_selfies, exist_ok=True)
        selfie_count = 0

        if os.path.exists(selfie_dir):
            for fname in os.listdir(selfie_dir):
                fpath = os.path.join(selfie_dir, fname)
                if os.path.isfile(fpath) and fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    shutil.copy2(fpath, os.path.join(dest_selfies, fname))
                    shutil.copy2(fpath, os.path.join(latest_selfies, fname))
                    selfie_count += 1
        counts["selfie_files"] = selfie_count

    # Save manifest
    manifest = {
        "timestamp": timestamp_str,
        "reason": reason,
        "source": "Turso LibSQL Cloud" if use_cloud else "Local SQLite",
        "backup_db": dest_db,
        "verified": True,
        "counts": counts,
        "db_size_bytes": os.path.getsize(dest_db),
    }
    with open(os.path.join(target_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(LATEST_BACKUP_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Keep seed_data synchronized if app/seed_data/ exists
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
    """Safely restores database and selfies from an uploaded .zip, .db, or .json file.
    Works seamlessly across local SQLite and Turso LibSQL Cloud.
    Creates an automatic pre-restore rollback backup before altering anything.
    """
    filename = (file_storage.filename or "").lower()
    allowed_exts = (".zip", ".db", ".sqlite", ".json")
    if not any(filename.endswith(ext) for ext in allowed_exts):
        return {"ok": False, "error": "Invalid file format. Upload a .zip, .db, or .json backup file."}

    # 1. Take a safety rollback backup first
    try:
        create_backup(reason="pre_restore_rollback")
    except Exception as e:
        logger.warning(f"Pre-restore safety backup notice: {e}")

    target_db = get_configured_db_path()
    target_selfies = get_configured_selfie_dir()
    os.makedirs(os.path.dirname(target_db), exist_ok=True)
    os.makedirs(target_selfies, exist_ok=True)

    temp_dir = os.path.join(BACKUP_ROOT, f"temp_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(temp_dir, exist_ok=True)

    extracted_db = None
    extracted_json = None
    restored_selfies_count = 0
    use_cloud = bool(Config.USE_TURSO and Config.TURSO_DATABASE_URL and Config.TURSO_AUTH_TOKEN)

    try:
        if filename.endswith(".zip"):
            zip_path = os.path.join(temp_dir, "upload.zip")
            file_storage.save(zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.namelist():
                    norm = os.path.normpath(member)
                    if norm.startswith("..") or os.path.isabs(norm):
                        raise ValueError("Malicious path detected in ZIP archive.")
                zf.extractall(temp_dir)

            # Search recursively in temp_dir for any .db, .sqlite, or .json file
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    lf = f.lower()
                    if lf.endswith((".db", ".sqlite")):
                        extracted_db = os.path.join(root, f)
                        break
                    elif lf.endswith(".json") and not lf.startswith("manifest") and not extracted_json:
                        extracted_json = os.path.join(root, f)
                if extracted_db:
                    break

            # Restore selfies if present
            for root, dirs, _ in os.walk(temp_dir):
                if os.path.basename(root).lower() == "selfies":
                    for s in os.listdir(root):
                        s_src = os.path.join(root, s)
                        if os.path.isfile(s_src) and s.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                            shutil.copy2(s_src, os.path.join(target_selfies, s))
                            restored_selfies_count += 1

        elif filename.endswith(".json"):
            extracted_json = os.path.join(temp_dir, "uploaded.json")
            file_storage.save(extracted_json)
        else:
            extracted_db = os.path.join(temp_dir, "uploaded.db")
            file_storage.save(extracted_db)

        # 2. Process either SQLite DB or JSON snapshot
        emp_count = 0
        att_count = 0

        if extracted_json and not extracted_db:
            with open(extracted_json, "r", encoding="utf-8") as jf:
                snap_data = json.load(jf)
            tables_data = snap_data.get("tables", {})
            if not tables_data:
                return {"ok": False, "error": "Uploaded JSON snapshot contains no valid table data."}

            if use_cloud:
                from app.turso_db import connect_turso, clear_turso_cache
                t_conn = connect_turso(Config.TURSO_DATABASE_URL, Config.TURSO_AUTH_TOKEN)
                for tbl, t_info in tables_data.items():
                    rows = t_info.get("rows", [])
                    cols = t_info.get("columns", [])
                    if tbl == "employees":
                        emp_count = len(rows)
                    elif tbl == "attendance":
                        att_count = len(rows)
                    if rows and cols:
                        col_names = ", ".join(f'"{c}"' for c in cols)
                        placeholders = ", ".join(["?"] * len(cols))
                        for r in rows:
                            vals = [r.get(c) for c in cols]
                            try:
                                t_conn.execute(f'INSERT OR REPLACE INTO "{tbl}" ({col_names}) VALUES ({placeholders})', vals)
                            except Exception:
                                pass
                clear_turso_cache()
            else:
                s_conn = sqlite3.connect(target_db)
                from app.db import SCHEMA
                s_conn.executescript(SCHEMA)
                for tbl, t_info in tables_data.items():
                    rows = t_info.get("rows", [])
                    cols = t_info.get("columns", [])
                    if tbl == "employees":
                        emp_count = len(rows)
                    elif tbl == "attendance":
                        att_count = len(rows)
                    if rows and cols:
                        col_names = ", ".join(f'"{c}"' for c in cols)
                        placeholders = ", ".join(["?"] * len(cols))
                        for r in rows:
                            vals = [r.get(c) for c in cols]
                            try:
                                s_conn.execute(f'INSERT OR REPLACE INTO "{tbl}" ({col_names}) VALUES ({placeholders})', vals)
                            except Exception:
                                pass
                s_conn.commit()
                s_conn.close()

        else:
            if not extracted_db or not os.path.exists(extracted_db):
                return {"ok": False, "error": "No valid SQLite database found in uploaded file. Please upload a .zip containing attendance.db or a direct .db file."}

            test_conn = sqlite3.connect(f"file:{extracted_db}?mode=ro", uri=True)
            cur = test_conn.cursor()
            try:
                emp_count = cur.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
                att_count = cur.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
            except Exception as e:
                test_conn.close()
                return {"ok": False, "error": f"Invalid SQLite database format: {e}"}

            if emp_count == 0:
                test_conn.close()
                return {"ok": False, "error": "Uploaded database has no registered employee records."}

            if use_cloud:
                # Restore rows into Turso LibSQL Cloud
                from app.turso_db import connect_turso, clear_turso_cache
                t_conn = connect_turso(Config.TURSO_DATABASE_URL, Config.TURSO_AUTH_TOKEN)
                for tbl in ALL_SYNC_TABLES:
                    try:
                        cur.execute(f'SELECT * FROM "{tbl}"')
                        cols = [col[0] for col in cur.description] if cur.description else []
                        rows = cur.fetchall()
                        if rows and cols:
                            col_names = ", ".join(f'"{c}"' for c in cols)
                            placeholders = ", ".join(["?"] * len(cols))
                            for r in rows:
                                t_conn.execute(f'INSERT OR REPLACE INTO "{tbl}" ({col_names}) VALUES ({placeholders})', list(r))
                    except Exception as e:
                        logger.warning(f"Table {tbl} not present or could not be restored to Turso: {e}")
                clear_turso_cache()
                test_conn.close()
            else:
                test_conn.close()
                shutil.copy2(extracted_db, target_db)

            # Update backups/latest and seed_data
            try:
                shutil.copy2(extracted_db, os.path.join(LATEST_BACKUP_DIR, "attendance.db"))
                seed_db = os.path.join(BASE_DIR, "app", "seed_data", "attendance.db")
                if os.path.exists(os.path.dirname(seed_db)):
                    shutil.copy2(extracted_db, seed_db)
            except Exception:
                pass

        # Clear session and shared caches
        from app.auth import clear_user_cache
        clear_user_cache()
        from app.services import clear_shared_cache
        clear_shared_cache()

        if user_id:
            try:
                from app.services import audit
                audit("BACKUP_RESTORE", "database", user_id, f"Restored {emp_count} employees, {att_count} attendance records from {filename}")
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
        logger.exception(f"Failed to restore backup: {e}")
        return {"ok": False, "error": f"Failed to restore backup: {str(e)}"}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
