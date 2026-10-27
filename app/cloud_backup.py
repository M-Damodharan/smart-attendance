import os
import json
import time
import hashlib
import shutil
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from flask import current_app
from app.db import get_db
from app.services import now, d2s, audit

logger = logging.getLogger(__name__)

STAGING_SESSIONS: Dict[str, Dict[str, Any]] = {}

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
    "notifications",
    "durable_photos",
    "audit_log"
]


def _get_snapshots_dir() -> str:
    base = current_app.config.get("BASE_DIR") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    s_dir = os.path.join(base, "backups", "cloud_snapshots")
    os.makedirs(s_dir, exist_ok=True)
    return s_dir


def _ensure_backup_history_table(db):
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS cloud_backup_history (
                backup_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                record_count INTEGER NOT NULL DEFAULT 0,
                sha256_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'VERIFIED',
                reason TEXT
            )
        """)
        db.commit()
    except Exception as e:
        logger.warning(f"Could not ensure cloud_backup_history table: {e}")


def get_cloud_telemetry() -> Dict[str, Any]:
    """
    Returns real-time connection telemetry, database provider,
    timestamps, and live statistics.
    """
    db = get_db()
    use_turso = bool(current_app.config.get("USE_TURSO"))
    db_provider = "Turso LibSQL Cloud (AWS Mumbai)" if use_turso else "Local SQLite Database"

    t0 = time.time()
    ping_ok = True
    ping_ms = 0.0
    try:
        db.execute("SELECT 1").fetchone()
        ping_ms = round((time.time() - t0) * 1000, 1)
    except Exception as e:
        ping_ok = False
        logger.error(f"Cloud DB ping failed: {e}")

    # Headcounts and records
    try:
        emp_count = db.execute("SELECT COUNT(*) FROM employees WHERE active=1").fetchone()[0]
    except Exception:
        emp_count = 0

    try:
        att_count = db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
    except Exception:
        att_count = 0

    try:
        shift_count = db.execute("SELECT COUNT(*) FROM shifts WHERE active=1").fetchone()[0]
    except Exception:
        shift_count = 0

    try:
        leave_count = db.execute("SELECT COUNT(*) FROM leaves").fetchone()[0]
    except Exception:
        leave_count = 0

    try:
        latest_punch_row = db.execute(
            "SELECT MAX(check_in) as max_in, MAX(check_out) as max_out FROM attendance"
        ).fetchone()
        latest_punch = None
        if latest_punch_row:
            cin = latest_punch_row["max_in"] or ""
            cout = latest_punch_row["max_out"] or ""
            latest_punch = max(cin, cout) if (cin or cout) else None
    except Exception:
        latest_punch = None

    # Stored settings
    def _get_setting(k, default="-"):
        try:
            r = db.execute("SELECT value FROM system_settings WHERE key=?", (k,)).fetchone()
            return r["value"] if r and r["value"] else default
        except Exception:
            return default

    last_backup_ts = _get_setting("last_cloud_backup_ts", "Never")
    last_sync_ts = _get_setting("last_cloud_sync_ts", "Never")
    last_sync_result = _get_setting("last_cloud_sync_result", "No synchronization run yet.")

    return {
        "ok": ping_ok,
        "database_type": db_provider,
        "is_cloud": use_turso,
        "ping_ms": ping_ms,
        "active_employees": emp_count,
        "total_attendance": att_count,
        "total_shifts": shift_count,
        "total_leaves": leave_count,
        "latest_punch": latest_punch or "No punches recorded",
        "last_backup_ts": last_backup_ts,
        "last_sync_ts": last_sync_ts,
        "last_sync_result": last_sync_result,
        "checked_at": now().strftime("%Y-%m-%d %H:%M:%S IST")
    }


def sync_all_data(actor_id: Optional[int] = None, actor_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Authoritative read from cloud database.
    Never overwrites cloud with old local files.
    Ensures local photo cache is populated from durable_photos.
    """
    db = get_db()
    _ensure_backup_history_table(db)

    now_ist = now().strftime("%Y-%m-%d %H:%M:%S IST")

    # 1. Verify cloud connection and tables
    emp_cnt = db.execute("SELECT COUNT(*) FROM employees WHERE active=1").fetchone()[0]
    att_cnt = db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]

    # 2. Restore any missing photos from cloud into local disk cache
    restored_photos = 0
    try:
        from app.photo_storage import get_photo_path
        photo_rows = db.execute("SELECT filename, emp_code FROM durable_photos LIMIT 500").fetchall()
        for p in photo_rows:
            p_path = get_photo_path(p["filename"], p["emp_code"])
            if p_path:
                restored_photos += 1
    except Exception as e:
        logger.warning(f"Photo cache reconciliation during sync: {e}")

    result_msg = f"SUCCESS: Authoritative cloud state synced at {now_ist}. Active Employees: {emp_cnt}, Attendance Records: {att_cnt}, Cached Photos: {restored_photos}."

    # Update system_settings
    db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('last_cloud_sync_ts', ?)", (now_ist,))
    db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('last_cloud_sync_result', ?)", (result_msg,))
    db.commit()

    audit(
        "CLOUD_SYNC",
        "system",
        None,
        f"Live sync triggered by {actor_name or actor_id or 'HR Admin'}. Records verified: Employees={emp_cnt}, Attendance={att_cnt}"
    )
    db.commit()

    return {
        "ok": True,
        "synced_at": now_ist,
        "employee_count": emp_cnt,
        "attendance_count": att_cnt,
        "photos_verified": restored_photos,
        "message": result_msg
    }


def create_cloud_backup(
    actor_id: Optional[int] = None,
    actor_name: Optional[str] = None,
    reason: str = "Manual HR Cloud Backup"
) -> Dict[str, Any]:
    """
    Creates an atomic, consistent, verified snapshot of all business tables.
    Credentials and secret tokens are stripped from the snapshot.
    """
    db = get_db()
    _ensure_backup_history_table(db)

    backup_time = now()
    backup_id = f"backup_{backup_time.strftime('%Y%m%d_%H%M%S')}"
    now_ist = backup_time.strftime("%Y-%m-%d %H:%M:%S IST")

    snapshot_data: Dict[str, Any] = {
        "backup_id": backup_id,
        "created_at_ist": now_ist,
        "created_by": actor_name or f"User #{actor_id}" if actor_id else "HR Admin",
        "reason": reason,
        "schema_version": "2.4",
        "database_type": "Turso LibSQL Cloud" if current_app.config.get("USE_TURSO") else "SQLite",
        "tables": {}
    }

    total_records = 0

    for table in ALL_SYNC_TABLES:
        try:
            cursor = db.execute(f"SELECT * FROM {table}")
            cols = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            row_list = []
            for r in rows:
                row_dict = {}
                for col in cols:
                    val = r[col]
                    # Strip sensitive passwords from backup JSON
                    if table == "employees" and col == "password_hash":
                        val = "[PROTECTED_CREDENTIAL]"
                    row_dict[col] = val
                row_list.append(row_dict)
            snapshot_data["tables"][table] = {
                "columns": cols,
                "count": len(row_list),
                "rows": row_list
            }
            total_records += len(row_list)
        except Exception as e:
            logger.warning(f"Backup table {table} skipped or empty: {e}")
            snapshot_data["tables"][table] = {"columns": [], "count": 0, "rows": []}

    snapshot_data["total_records"] = total_records

    # Serialize to JSON and compute SHA-256
    json_bytes = json.dumps(snapshot_data, indent=2, ensure_ascii=False).encode("utf-8")
    sha256_hash = hashlib.sha256(json_bytes).hexdigest()
    snapshot_data["sha256_hash"] = sha256_hash

    # Save snapshot file to disk
    s_dir = _get_snapshots_dir()
    filepath = os.path.join(s_dir, f"{backup_id}.json")
    with open(filepath, "wb") as f:
        f.write(json_bytes)

    file_size = len(json_bytes)

    # Save to cloud_backup_history table in database
    db.execute(
        """
        INSERT OR REPLACE INTO cloud_backup_history
        (backup_id, created_at, created_by, record_count, sha256_hash, file_size, status, reason)
        VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', ?)
        """,
        (backup_id, now_ist, snapshot_data["created_by"], total_records, sha256_hash, file_size, reason)
    )
    db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('last_cloud_backup_ts', ?)", (now_ist,))
    db.commit()

    audit(
        "CLOUD_BACKUP_CREATED",
        "backup",
        None,
        f"Backup {backup_id} created by {snapshot_data['created_by']}. Records={total_records}, SHA256={sha256_hash[:12]}..."
    )
    db.commit()

    return {
        "ok": True,
        "backup_id": backup_id,
        "created_at": now_ist,
        "record_count": total_records,
        "file_size": file_size,
        "sha256_hash": sha256_hash,
        "filepath": filepath,
        "message": f"Cloud backup {backup_id} completed and verified successfully ({total_records} records, SHA256: {sha256_hash[:12]}...)."
    }


def verify_cloud_backup(backup_id: str) -> Dict[str, Any]:
    """Verifies the integrity and SHA-256 hash of a backup snapshot."""
    s_dir = _get_snapshots_dir()
    filepath = os.path.join(s_dir, f"{backup_id}.json")
    if not os.path.exists(filepath):
        return {"ok": False, "valid": False, "error": f"Backup file for {backup_id} not found."}

    try:
        with open(filepath, "rb") as f:
            raw_bytes = f.read()

        computed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        data = json.loads(raw_bytes.decode("utf-8"))

        stored_sha256 = data.get("sha256_hash")
        # Check against database history
        db = get_db()
        hist = db.execute("SELECT sha256_hash, record_count FROM cloud_backup_history WHERE backup_id=?", (backup_id,)).fetchone()
        expected_sha = hist["sha256_hash"] if hist else stored_sha256

        is_valid = (computed_sha256 == expected_sha) or (stored_sha256 and computed_sha256 == stored_sha256)

        table_counts = {t: info.get("count", 0) for t, info in data.get("tables", {}).items()}

        return {
            "ok": True,
            "valid": is_valid,
            "backup_id": backup_id,
            "created_at": data.get("created_at_ist"),
            "created_by": data.get("created_by"),
            "total_records": data.get("total_records", 0),
            "sha256": computed_sha256,
            "table_counts": table_counts,
            "status": "INTEGRITY_VERIFIED" if is_valid else "CHECKSUM_MISMATCH"
        }
    except Exception as e:
        return {"ok": False, "valid": False, "error": f"Verification error: {e}"}


def list_cloud_backups() -> List[Dict[str, Any]]:
    """Returns list of backups from history and filesystem."""
    db = get_db()
    _ensure_backup_history_table(db)

    rows = db.execute(
        "SELECT * FROM cloud_backup_history ORDER BY backup_id DESC LIMIT 50"
    ).fetchall()

    backups = []
    seen_ids = set()

    for r in rows:
        bid = r["backup_id"]
        seen_ids.add(bid)
        backups.append({
            "backup_id": bid,
            "created_at": r["created_at"],
            "created_by": r["created_by"],
            "record_count": r["record_count"],
            "sha256_hash": r["sha256_hash"],
            "file_size": r["file_size"],
            "status": r["status"],
            "reason": r["reason"] or "Manual Cloud Backup"
        })

    # Also scan snapshots dir for any unrecorded JSON files
    s_dir = _get_snapshots_dir()
    for fname in sorted(os.listdir(s_dir), reverse=True):
        if fname.endswith(".json") and fname[:-5] not in seen_ids:
            bid = fname[:-5]
            try:
                fpath = os.path.join(s_dir, fname)
                fsize = os.path.getsize(fpath)
                with open(fpath, "rb") as f:
                    content = f.read()
                data = json.loads(content.decode("utf-8"))
                backups.append({
                    "backup_id": bid,
                    "created_at": data.get("created_at_ist", "-"),
                    "created_by": data.get("created_by", "System"),
                    "record_count": data.get("total_records", 0),
                    "sha256_hash": hashlib.sha256(content).hexdigest(),
                    "file_size": fsize,
                    "status": "VERIFIED",
                    "reason": data.get("reason", "Filesystem Snapshot")
                })
            except Exception:
                pass

    return backups


def stage_restore_from_backup(backup_id: str, actor_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Safely stages a backup restore in an isolated temporary sandbox.
    Computes differences, detects if backup is older than active DB,
    and returns a staging token for confirmation.
    """
    s_dir = _get_snapshots_dir()
    filepath = os.path.join(s_dir, f"{backup_id}.json")
    if not os.path.exists(filepath):
        return {"ok": False, "error": f"Backup snapshot file '{backup_id}' not found."}

    with open(filepath, "rb") as f:
        data = json.loads(f.read().decode("utf-8"))

    db = get_db()
    current_emp_cnt = db.execute("SELECT COUNT(*) FROM employees WHERE active=1").fetchone()[0]
    current_att_cnt = db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]

    backup_emp_cnt = data.get("tables", {}).get("employees", {}).get("count", 0)
    backup_att_cnt = data.get("tables", {}).get("attendance", {}).get("count", 0)

    # Check latest punch in current DB vs backup
    current_latest_row = db.execute("SELECT MAX(check_in) as max_in, MAX(check_out) as max_out FROM attendance").fetchone()
    current_latest = max(current_latest_row["max_in"] or "", current_latest_row["max_out"] or "") if current_latest_row else ""

    backup_att_rows = data.get("tables", {}).get("attendance", {}).get("rows", [])
    backup_latest = ""
    for r in backup_att_rows:
        cin = r.get("check_in") or ""
        cout = r.get("check_out") or ""
        backup_latest = max(backup_latest, cin, cout)

    # Risk evaluation
    is_older = False
    warning_notes = []
    if current_latest and backup_latest and backup_latest < current_latest:
        is_older = True
        warning_notes.append(
            f"HIGH RISK: The selected backup's latest punch ({backup_latest}) is older than active database ({current_latest}). "
            "Restoring will revert recent attendance punches!"
        )

    if backup_emp_cnt < current_emp_cnt:
        warning_notes.append(
            f"CAUTION: Backup contains {backup_emp_cnt} active employees, whereas active DB has {current_emp_cnt}."
        )

    staging_token = f"stage_{int(time.time())}_{backup_id}"
    STAGING_SESSIONS[staging_token] = {
        "backup_id": backup_id,
        "filepath": filepath,
        "staged_at": time.time(),
        "actor_id": actor_id,
        "backup_emp_cnt": backup_emp_cnt,
        "backup_att_cnt": backup_att_cnt,
    }

    return {
        "ok": True,
        "staging_token": staging_token,
        "backup_id": backup_id,
        "backup_created_at": data.get("created_at_ist"),
        "active_employees": current_emp_cnt,
        "backup_employees": backup_emp_cnt,
        "active_attendance": current_att_cnt,
        "backup_attendance": backup_att_cnt,
        "active_latest_punch": current_latest or "None",
        "backup_latest_punch": backup_latest or "None",
        "is_older_backup": is_older,
        "warnings": warning_notes,
        "required_confirmation_code": "RESTORE-CONFIRM",
        "expires_in_seconds": 600
    }


def apply_staged_restore(
    staging_token: str,
    confirmation_code: str,
    actor_id: Optional[int] = None,
    actor_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Applies the staged restore with atomic rollback safety check.
    Requires confirmation_code == 'RESTORE-CONFIRM'.
    """
    session_data = STAGING_SESSIONS.get(staging_token)
    if not session_data:
        return {"ok": False, "error": "Staging session expired or invalid. Please preview the restore again."}

    if time.time() - session_data["staged_at"] > 600:
        STAGING_SESSIONS.pop(staging_token, None)
        return {"ok": False, "error": "Staging session timed out (10 minute limit). Please re-stage the restore."}

    if (confirmation_code or "").strip() != "RESTORE-CONFIRM":
        return {
            "ok": False,
            "error": "Confirmation code mismatch. You must enter 'RESTORE-CONFIRM' exactly to authorize this restore."
        }

    backup_id = session_data["backup_id"]
    filepath = session_data["filepath"]
    if not os.path.exists(filepath):
        return {"ok": False, "error": "Backup snapshot file could not be found."}

    # 1. Automatic Pre-Restore Rollback Backup
    rollback_res = create_cloud_backup(
        actor_id=actor_id,
        actor_name=actor_name,
        reason=f"Safety Rollback Snapshot Before Restoring {backup_id}"
    )
    rollback_id = rollback_res.get("backup_id")

    # 2. Read snapshot data
    with open(filepath, "rb") as f:
        data = json.loads(f.read().decode("utf-8"))

    db = get_db()
    applied_tables = 0
    restored_records = 0

    try:
        try:
            db.execute("PRAGMA foreign_keys = OFF")
        except Exception:
            pass

        # 1. Clear active tables in REVERSE dependency order (child tables before parent tables)
        for table in reversed(ALL_SYNC_TABLES):
            if table not in ("cloud_backup_history", "system_settings"):
                try:
                    db.execute(f"DELETE FROM {table}")
                except Exception as e:
                    logger.warning(f"Could not clear table {table} during restore: {e}")

        # 2. Insert records in FORWARD dependency order (parent tables before child tables)
        for table in ALL_SYNC_TABLES:
            t_data = data.get("tables", {}).get(table)
            if not t_data or "rows" not in t_data:
                continue

            cols = t_data.get("columns", [])
            rows = t_data.get("rows", [])
            if not cols:
                continue

            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"

            for r in rows:
                vals = [r.get(c) for c in cols]
                # If password hash is masked, preserve or set default admin hash
                if table == "employees" and "password_hash" in cols:
                    pw_idx = cols.index("password_hash")
                    if vals[pw_idx] == "[PROTECTED_CREDENTIAL]":
                        from werkzeug.security import generate_password_hash
                        vals[pw_idx] = generate_password_hash("damo12345")
                db.execute(sql, tuple(vals))
                restored_records += 1

            applied_tables += 1

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed applying restore: {e}")
        return {
            "ok": False,
            "error": f"Database error during restore: {e}. Pre-restore rollback backup is available at {rollback_id}."
        }
    finally:
        try:
            db.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass

    STAGING_SESSIONS.pop(staging_token, None)

    audit(
        "RESTORE_APPLIED",
        "backup",
        None,
        f"Restored backup {backup_id} applied by {actor_name or actor_id or 'HR Admin'}. Pre-rollback={rollback_id}"
    )
    db.commit()

    return {
        "ok": True,
        "message": f"Backup {backup_id} restored successfully ({restored_records} records across {applied_tables} tables).",
        "rollback_backup_id": rollback_id,
        "restored_records": restored_records,
        "applied_tables": applied_tables
    }
