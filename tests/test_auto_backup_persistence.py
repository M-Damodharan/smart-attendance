import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app import create_app
from app.db import get_db, init_db
from app.backup_manager import (
    create_backup,
    list_backups,
    build_backup_zip_bytes,
    restore_backup,
    BACKUP_ROOT,
    LATEST_BACKUP_DIR,
)


class TestAutoBackupPersistence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_backup.db")
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

    def test_01_create_backup_and_list_backups(self):
        """create_backup creates an atomic SQLite copy, manifest, and updates latest backup."""
        res = create_backup(reason="test_snapshot")
        self.assertTrue(res.get("ok"))
        self.assertIn("counts", res)
        self.assertGreater(res["counts"].get("employees", 0), 0)

        # Verify backups/latest exists
        latest_db = os.path.join(LATEST_BACKUP_DIR, "attendance.db")
        self.assertTrue(os.path.exists(latest_db))
        self.assertGreater(os.path.getsize(latest_db), 0)

        # Verify list_backups finds the new backup
        backups = list_backups()
        self.assertGreaterEqual(len(backups), 1)
        latest_item = backups[0]
        self.assertIn("emp_count", latest_item)
        self.assertGreater(latest_item["emp_count"], 0)

    def test_02_build_backup_zip_contains_db_and_manifest(self):
        """build_backup_zip_bytes must generate a valid zip containing attendance.db and manifest."""
        zip_bytes = build_backup_zip_bytes()
        self.assertIsInstance(zip_bytes, bytes)
        self.assertGreater(len(zip_bytes), 1000)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = zf.namelist()
            self.assertIn("attendance.db", namelist)
            self.assertIn("manifest.json", namelist)

    def test_03_restore_backup_rejects_invalid_file(self):
        """restore_backup strictly rejects non-sqlite/non-zip corrupted uploads."""
        dummy_file = io.BytesIO(b"this is not a database")
        dummy_file.filename = "corrupted.txt"
        res = restore_backup(dummy_file, user_id=1)
        self.assertFalse(res.get("ok"))
        self.assertIn("Invalid file format", res.get("error", ""))

    def test_04_download_and_restore_backup_via_hr_routes(self):
        """HR can download the full backup ZIP and upload to restore."""
        self._login(self.client, "HR001", "damo12345")

        # 1. Download backup ZIP
        res_dl = self.client.get("/hr/database/download-backup")
        self.assertEqual(res_dl.status_code, 200)
        self.assertEqual(res_dl.mimetype, "application/zip")
        self.assertIn("SBG_Attendance_Backup_", res_dl.headers.get("Content-Disposition", ""))

        # 2. Re-upload and restore the downloaded zip
        downloaded_zip = io.BytesIO(res_dl.data)
        downloaded_zip.filename = "test_restore.zip"
        res_restore = self.client.post(
            "/hr/database/restore-backup",
            data={"backup_file": (downloaded_zip, "test_restore.zip")},
            content_type="multipart/form-data",
            follow_redirects=True
        )
        self.assertEqual(res_restore.status_code, 200)
        html = res_restore.data.decode("utf-8")
        self.assertIn("Database successfully restored", html)

    def test_05_hr_settings_displays_backup_card_and_snapshots(self):
        """HR settings page displays the Database Backup & Disaster Recovery card."""
        self._login(self.client, "HR001", "damo12345")
        res = self.client.get("/hr/settings")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Database Backup &amp; Disaster Recovery", html)
        self.assertIn("Download Full Backup (.ZIP)", html)
        self.assertIn("Upload &amp; Restore Database Backup", html)
        self.assertIn("id=\"backups\"", html)

    def test_06_auto_recovery_when_db_is_absent(self):
        """init_db must recover from seed_data or latest backup if the active database is empty/missing."""
        test_dir = tempfile.mkdtemp()
        try:
            missing_db = os.path.join(test_dir, "missing.db")
            app_recovery = create_app()
            app_recovery.config["TESTING"] = True
            app_recovery.config["FORCE_RECOVERY_TEST"] = True
            app_recovery.config["DATABASE"] = missing_db
            app_recovery.config["SELFIE_DIR"] = os.path.join(test_dir, "selfies")

            # Run init_db on missing file
            init_db(app_recovery)
            self.assertTrue(os.path.exists(missing_db))
            self.assertGreater(os.path.getsize(missing_db), 0)

            # Check that employees table has records restored from seed or baseline
            with app_recovery.app_context():
                db = get_db()
                emp_cnt = db.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
                self.assertGreater(emp_cnt, 0)
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
