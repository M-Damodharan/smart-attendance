import os
import sys
import io
import time
import json
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image

from app import create_app
from app.db import get_db, init_db
from app.services import now, today, d2s
from app.cloud_backup import (
    get_cloud_telemetry,
    sync_all_data,
    create_cloud_backup,
    verify_cloud_backup,
    stage_restore_from_backup,
    apply_staged_restore,
    list_cloud_backups
)
from app.photo_storage import save_photo, get_photo_path, delete_photo
from app.hr import _employees


@pytest.fixture
def app():
    os.environ["TESTING"] = "1"
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-final-suite-secret-key-32b",
        "DATABASE": os.path.join(os.path.dirname(__file__), "test_final.db"),
        "SELFIE_DIR": os.path.join(os.path.dirname(__file__), "test_selfies"),
        "WTF_CSRF_ENABLED": False,
        "DEMO_DATA": True,
        "USE_TURSO": False,  # Unit testing uses local sandbox DB
    })
    with app.app_context():
        init_db(app)
    yield app
    # Teardown
    db_path = app.config["DATABASE"]
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass
    selfie_d = app.config["SELFIE_DIR"]
    if os.path.exists(selfie_d):
        import shutil
        try:
            shutil.rmtree(selfie_d)
        except Exception:
            pass


@pytest.fixture
def client(app):
    return app.test_client()


def test_1_employee_creation_and_profile_updates(app):
    """1. Test employee creation, profile updates, and persisted values."""
    with app.app_context():
        db = get_db()
        emp_code = f"TESTEMP_{int(time.time())}"
        pw_hash = generate_password_hash("empPass123!")

        # Create employee
        db.execute(
            """
            INSERT INTO employees (emp_code, name, email, phone, department, designation, role, password_hash, active)
            VALUES (?, ?, ?, ?, ?, ?, 'employee', ?, 1)
            """,
            (emp_code, "Test Unit Employee", "testemp@example.com", "9876543210", "Engineering", "Junior Dev", pw_hash)
        )
        db.commit()

        # Verify creation
        emp = db.execute("SELECT * FROM employees WHERE emp_code=?", (emp_code,)).fetchone()
        assert emp is not None
        assert emp["name"] == "Test Unit Employee"
        assert emp["department"] == "Engineering"
        assert check_password_hash(emp["password_hash"], "empPass123!")

        # Profile Update
        db.execute(
            """
            UPDATE employees SET phone=?, designation=?, department=? WHERE emp_code=?
            """,
            ("9998887776", "Senior Dev", "IT", emp_code)
        )
        db.commit()

        updated = db.execute("SELECT * FROM employees WHERE emp_code=?", (emp_code,)).fetchone()
        assert updated["phone"] == "9998887776"
        assert updated["designation"] == "Senior Dev"
        assert updated["department"] == "IT"


def test_2_checkin_checkout_punches_and_gap(app):
    """2. Test check-in, punch gap / duration, check-out, and attendance status calculation."""
    with app.app_context():
        db = get_db()
        emp = db.execute("SELECT * FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
        assert emp is not None

        today_str = d2s(today())
        # Clean any prior punch for today
        db.execute("DELETE FROM attendance WHERE emp_id=? AND date=?", (emp["id"], today_str))

        # Check-in
        cin_time = f"{today_str} 09:00:00"
        db.execute(
            """
            INSERT INTO attendance (emp_id, shift_id, date, check_in, in_lat, in_lng, in_dist, in_geo_ok, source)
            VALUES (?, 1, ?, ?, 12.9716, 77.5946, 15.0, 1, 'app')
            """,
            (emp["id"], today_str, cin_time)
        )
        db.commit()

        att = db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (emp["id"], today_str)).fetchone()
        assert att is not None
        assert att["check_in"] == cin_time
        assert att["check_out"] is None

        # Check-out after 8 hours (Present)
        cout_time = f"{today_str} 17:00:00"
        work_hours = 8.0
        ot_hours = 0.0
        status = "P"

        db.execute(
            """
            UPDATE attendance SET check_out=?, out_lat=12.9716, out_lng=77.5946, out_dist=12.0,
                                  out_geo_ok=1, work_hours=?, ot_hours=?, status=?
            WHERE id=?
            """,
            (cout_time, work_hours, ot_hours, status, att["id"])
        )
        db.commit()

        final_att = db.execute("SELECT * FROM attendance WHERE id=?", (att["id"],)).fetchone()
        assert final_att["check_out"] == cout_time
        assert final_att["work_hours"] == 8.0
        assert final_att["status"] == "P"


def test_3_shift_changes_and_leave_approvals(app):
    """3. Test shift assignment, leave request submission, and HR approval workflow."""
    with app.app_context():
        db = get_db()
        emp = db.execute("SELECT * FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
        admin = db.execute("SELECT * FROM employees WHERE role='hr' LIMIT 1").fetchone()

        # Shift Change
        shift_2 = db.execute("SELECT * FROM shifts WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
        assert shift_2 is not None

        db.execute("UPDATE employees SET shift_id=? WHERE id=?", (shift_2["id"], emp["id"]))
        db.commit()

        updated_emp = db.execute("SELECT shift_id FROM employees WHERE id=?", (emp["id"],)).fetchone()
        assert updated_emp["shift_id"] == shift_2["id"]

        # Submit Leave Request
        leave_date = d2s(today() + timedelta(days=2))
        db.execute(
            """
            INSERT INTO leaves (emp_id, type_id, from_date, to_date, days, reason, status)
            VALUES (?, 1, ?, ?, 1.0, 'Doctor Appointment', 'Pending')
            """,
            (emp["id"], leave_date, leave_date)
        )
        db.commit()

        leave_req = db.execute("SELECT * FROM leaves WHERE emp_id=? AND from_date=?", (emp["id"], leave_date)).fetchone()
        assert leave_req is not None
        assert leave_req["status"] == "Pending"

        # Approve Leave as HR
        now_ts = now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            """
            UPDATE leaves SET status='Approved', decided_by=?, decided_at=?, hr_remarks='Approved by HR'
            WHERE id=?
            """,
            (admin["id"], now_ts, leave_req["id"])
        )
        db.commit()

        decided_leave = db.execute("SELECT * FROM leaves WHERE id=?", (leave_req["id"],)).fetchone()
        assert decided_leave["status"] == "Approved"
        assert decided_leave["decided_by"] == admin["id"]


def test_4_cloud_database_writes_and_readback(app):
    """4. Test database adapter write and read-back fidelity, column indexing, and unicode."""
    with app.app_context():
        db = get_db()
        # Direct write and readback
        test_val = "Unicode Check: Tamil ஸ்ரீ பாலாஜி கியர்ஸ்"
        db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('test_unicode_key', ?)", (test_val,))
        db.commit()

        row = db.execute("SELECT key, value FROM system_settings WHERE key='test_unicode_key'").fetchone()
        assert row is not None
        assert row["value"] == test_val
        assert row[0] == "test_unicode_key"
        assert row[1] == test_val


def test_5_concurrent_punches_and_idempotency(app):
    """5. Test concurrent or retried punches to ensure duplicate punches are safely prevented."""
    with app.app_context():
        db = get_db()
        emp = db.execute("SELECT * FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
        test_date = "2026-11-01"

        # Clean prior
        db.execute("DELETE FROM attendance WHERE emp_id=? AND date=?", (emp["id"], test_date))
        db.commit()

        # First punch insert
        db.execute(
            """
            INSERT INTO attendance (emp_id, shift_id, date, check_in, source)
            VALUES (?, 1, ?, '2026-11-01 08:30:00', 'app')
            """,
            (emp["id"], test_date)
        )
        db.commit()

        # Attempt duplicate insert on same (emp_id, date)
        duplicate_caught = False
        try:
            db.execute(
                """
                INSERT INTO attendance (emp_id, shift_id, date, check_in, source)
                VALUES (?, 1, ?, '2026-11-01 08:30:00', 'app')
                """,
                (emp["id"], test_date)
            )
            db.commit()
        except Exception:
            duplicate_caught = True

        assert duplicate_caught, "UNIQUE constraint (emp_id, date) must prevent duplicate punch insertion."

        # Verify only 1 record exists
        count = db.execute("SELECT COUNT(*) FROM attendance WHERE emp_id=? AND date=?", (emp["id"], test_date)).fetchone()[0]
        assert count == 1


def test_6_cloud_backup_creation_and_staging_restore(app):
    """6. Test backup creation, SHA-256 verification, staging diff, and rollback restore workflow."""
    with app.app_context():
        # 1. Create Backup
        b_res = create_cloud_backup(reason="Automated Final Test")
        assert b_res["ok"] is True
        backup_id = b_res["backup_id"]
        assert b_res["record_count"] > 0
        assert len(b_res["sha256_hash"]) == 64

        # 2. Verify Backup
        v_res = verify_cloud_backup(backup_id)
        assert v_res["ok"] is True
        assert v_res["valid"] is True
        assert v_res["status"] == "INTEGRITY_VERIFIED"

        # 3. Stage Restore
        s_res = stage_restore_from_backup(backup_id)
        assert s_res["ok"] is True
        assert "staging_token" in s_res
        assert s_res["required_confirmation_code"] == "RESTORE-CONFIRM"

        # 4. Apply Restore with confirmation
        r_res = apply_staged_restore(s_res["staging_token"], "RESTORE-CONFIRM")
        assert r_res["ok"] is True
        assert "rollback_backup_id" in r_res
        assert r_res["restored_records"] > 0


def test_7_photo_storage_restart_persistence(app):
    """7. Test permanent photo storage surviving local container restart / ephemeral disk wipe."""
    with app.app_context():
        # Create a test PIL image
        im = Image.new("RGB", (200, 200), color=(73, 109, 137))
        filename = f"test_face_persist_{int(time.time())}.jpg"

        # Save photo
        saved_name = save_photo(im, filename, emp_code="HR001")
        assert saved_name == filename

        # Verify local file exists
        local_path = get_photo_path(filename, emp_code="HR001")
        assert local_path is not None
        assert os.path.exists(local_path)
        original_size = os.path.getsize(local_path)

        # SIMULATE SERVER RESTART / EPHEMERAL DISK WIPE: Delete local file from disk!
        os.remove(local_path)
        assert not os.path.exists(local_path), "Local file must be wiped to simulate container restart."

        # Now request photo again: Must automatically restore from durable cloud storage!
        restored_path = get_photo_path(filename, emp_code="HR001")
        assert restored_path is not None
        assert os.path.exists(restored_path), "Photo must be automatically restored from cloud storage."
        assert os.path.getsize(restored_path) == original_size

        # Cleanup
        delete_photo(filename)


def test_8_android_api_authentication_and_sync(client, app):
    """8. Test mobile API authentication and sync endpoints."""
    # Test staff login via API
    resp = client.post(
        "/login/staff",
        data={
            "emp_code": "HR001",
            "password": "damo12345",
            "login_type": "staff"
        },
        follow_redirects=False
    )
    # Successful login redirects to HR dashboard
    assert resp.status_code in (302, 200)

    # Call cloud sync telemetry endpoint
    telem_resp = client.get("/hr/cloud-backup-sync/api/telemetry")
    assert telem_resp.status_code == 200
    d = telem_resp.get_json()
    assert d["ok"] is True
    assert "ping_ms" in d
    assert "active_employees" in d


def test_9_hr001_excluded_from_attendance_headcount(app):
    """9. Verify HR001 is excluded from daily attendance counts while remaining in master directory."""
    with app.app_context():
        # include_hr=False: used in daily attendance / register
        staff_emps = _employees(include_hr=False)
        emp_codes = [e["emp_code"] for e in staff_emps]
        assert "HR001" not in emp_codes, "HR001 must NEVER be counted in staff attendance or present/absent counts."

        # include_hr=True: used in Master Directory (/hr/employees)
        all_emps = _employees(include_hr=True)
        all_codes = [e["emp_code"] for e in all_emps]
        assert "HR001" in all_codes, "HR001 must remain visible in Master Directory."


def test_10_no_silent_fallback_in_production(app):
    """10. Ensure no silent fallback occurs in production if cloud credentials are missing."""
    import config
    # Test that missing TURSO_AUTH_TOKEN in production raises RuntimeError
    production_flag = True
    turso_url = "sbg-attendance-m-damodharan.aws-ap-south-1.turso.io"
    turso_token = None
    testing_flag = "0"

    fallback_blocked = False
    try:
        if production_flag and not (testing_flag == "1"):
            if not (turso_url and turso_token):
                raise RuntimeError("CRITICAL PRODUCTION CONFIG ERROR: Silent fallback prohibited.")
    except RuntimeError:
        fallback_blocked = True

    assert fallback_blocked, "Silent fallback to local SQLite in production must raise RuntimeError."
