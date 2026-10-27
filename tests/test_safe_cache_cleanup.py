import json
import os
import shutil
import sys
import tempfile
import time
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app import create_app
from app.db import get_db, init_db
from app.maintenance import (
    is_path_safe_for_deletion,
    get_cache_status,
    safe_clean_cache,
    PROTECTED_DIRECTORIES,
    PROTECTED_EXTENSIONS,
)


class TestSafeCacheCleanup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_maintenance.db")
        cls.selfie_dir = os.path.join(cls.tmpdir, "selfies")
        os.makedirs(cls.selfie_dir, exist_ok=True)

        os.environ["DATABASE"] = cls.db_path
        os.environ["SELFIE_DIR"] = cls.selfie_dir
        os.environ["APP_ENV"] = "testing"
        os.environ["DEMO_DATA"] = "1"

        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.app.config["DATABASE"] = cls.db_path
        cls.app.config["SELFIE_DIR"] = cls.selfie_dir
        cls.app.config["DEMO_DATA"] = True

        init_db(cls.app)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _login(self, client, emp_code, password):
        client.get("/logout")
        return client.post("/login", data={"emp_code": emp_code, "password": password}, follow_redirects=True)

    def test_01_path_safety_validator_rejects_sensitive_locations(self):
        """Validator must strictly reject database files, instance dir, backups, and source code."""
        # 1. Database files must never be allowed
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "instance", "attendance.db")))
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "instance", "attendance.db-wal")))
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "instance", "attendance.db-shm")))
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "backups", "test_backup.db")))

        # 2. Photos, selfies, and templates must never be allowed
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "instance", "selfies", "punch_123.jpg")))
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "app", "static", "img", "logo.png")))

        # 3. Source code and templates must never be allowed
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "app", "maintenance.py")))
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "app", "templates", "hr", "settings.html")))

        # 4. Outside path traversal must be rejected
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "..", "secret.txt")))
        self.assertFalse(is_path_safe_for_deletion("C:\\Windows\\System32\\cmd.exe"))

        # 5. Virtualenv must not be touched
        self.assertFalse(is_path_safe_for_deletion(os.path.join(BASE_DIR, "venv", "Lib", "test.pyc")))

        # 6. Valid temporary bytecode file inside app/__pycache__/ must be accepted
        dummy_pyc = os.path.join(BASE_DIR, "app", "__pycache__", "dummy_test.cpython-311.pyc")
        self.assertTrue(is_path_safe_for_deletion(dummy_pyc))

    def test_02_database_and_records_remain_intact_after_cleanup(self):
        """safe_clean_cache must preserve 100% of employee, attendance, shift, and selfie records."""
        with self.app.app_context():
            db = get_db()
            
            # Count records before cleanup
            emp_count_before = db.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            att_count_before = db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
            shift_count_before = db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
            self.assertGreater(emp_count_before, 0, "Demo employees must exist")

            # Seed a dummy consumed challenge and an active challenge
            now_ts = time.time()
            db.execute(
                "INSERT INTO challenges (token, emp_id, prompts, created_at, used) VALUES (?, ?, ?, ?, ?)",
                ("old_expired_test_tok", 1, "blink", now_ts - 5000, 1)
            )
            db.execute(
                "INSERT INTO challenges (token, emp_id, prompts, created_at, used) VALUES (?, ?, ?, ?, ?)",
                ("active_valid_test_tok", 1, "smile", now_ts, 0)
            )
            db.commit()

            # Execute safe cache cleanup
            result = safe_clean_cache(db, user_id=1)

            # Verify output structure
            self.assertEqual(result["status"], "success")
            self.assertIn("pyc_files_removed", result)
            self.assertIn("pycache_dirs_removed", result)
            self.assertIn("bytes_freed", result)
            self.assertIn("expired_challenges_cleared", result)
            self.assertGreaterEqual(result["expired_challenges_cleared"], 1)

            # Count records after cleanup: MUST BE 100% IDENTICAL
            emp_count_after = db.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            att_count_after = db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
            shift_count_after = db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]

            self.assertEqual(emp_count_before, emp_count_after, "Employee count must not change")
            self.assertEqual(att_count_before, att_count_after, "Attendance count must not change")
            self.assertEqual(shift_count_before, shift_count_after, "Shift count must not change")

            # Verify active token is preserved, expired token is pruned
            active_tok = db.execute("SELECT * FROM challenges WHERE token='active_valid_test_tok'").fetchone()
            self.assertIsNotNone(active_tok, "Active challenge token must be preserved")
            old_tok = db.execute("SELECT * FROM challenges WHERE token='old_expired_test_tok'").fetchone()
            self.assertIsNone(old_tok, "Expired challenge token must be safely cleaned")

            # Verify audit log entry was written
            audit_entry = db.execute(
                "SELECT * FROM audit_log WHERE action='CACHE_CLEANUP' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(audit_entry, "Audit log entry must be created")
            self.assertEqual(audit_entry["entity"], "maintenance")

    def test_03_unauthorized_access_is_blocked(self):
        """Unauthenticated users and non-HR employees must be blocked from executing maintenance."""
        # 1. Unauthenticated request
        self.client.get("/logout")
        res_anon = self.client.post("/hr/maintenance/clear-cache", json={"confirm": "yes"})
        self.assertIn(res_anon.status_code, (302, 401, 403))

        # 2. Ordinary employee login (E001)
        self._login(self.client, "E001", "emp123")
        res_emp = self.client.post("/hr/maintenance/clear-cache", json={"confirm": "yes"})
        self.assertIn(res_emp.status_code, (302, 403))

    def test_04_hr_admin_can_trigger_clear_cache_via_json(self):
        """HR admin can trigger safe cache cleanup and receives detailed JSON metrics."""
        self._login(self.client, "HR001", "damo12345")
        res = self.client.post(
            "/hr/maintenance/clear-cache",
            data=json.dumps({"confirm": "yes"}),
            content_type="application/json",
            headers={"Accept": "application/json"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("status"), "success")
        self.assertIn("bytes_freed_formatted", data)
        self.assertIn("categories_cleared", data)

    def test_05_hr_settings_and_dashboard_pages_render_maintenance_ui(self):
        """HR settings and dashboard templates render the maintenance card and clear cache buttons."""
        self._login(self.client, "HR001", "damo12345")
        
        # 1. HR Settings page includes #maintenance card and safety guarantee
        res_settings = self.client.get("/hr/settings")
        self.assertEqual(res_settings.status_code, 200)
        html_settings = res_settings.data.decode("utf-8")
        self.assertIn("System Maintenance &amp; Safe Cache Cleanup", html_settings)
        self.assertIn("Safe Maintenance Guarantee:", html_settings)
        self.assertIn("id=\"maintenance\"", html_settings)
        self.assertIn("Clear Cache Now", html_settings)

        # 2. HR Dashboard includes quick shortcut button
        res_dash = self.client.get("/hr/")
        self.assertEqual(res_dash.status_code, 200)
        html_dash = res_dash.data.decode("utf-8")
        self.assertIn("🧹 Clear Cache", html_dash)
        self.assertTrue("hr.settings" in html_dash or "/hr/settings#maintenance" in html_dash)

    def test_06_idempotent_cleanup_runs_safely(self):
        """Calling safe_clean_cache multiple times must succeed without error."""
        with self.app.app_context():
            db = get_db()
            res1 = safe_clean_cache(db, user_id=1)
            res2 = safe_clean_cache(db, user_id=1)
            self.assertEqual(res1["status"], "success")
            self.assertEqual(res2["status"], "success")
            self.assertEqual(len(res2["errors"]), 0)


if __name__ == "__main__":
    unittest.main()
