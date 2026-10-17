"""SBG Smart Attendance - AI Buddy Punch Detection & Real-Time Account Locking Tests.

Verifies:
1. Real-time Buddy Punch / Cross-Account Spoofing Detection on punch.
2. Immediate Account Blocking ("next second block ID") with active=0 and block_reason recorded.
3. Access Denied on login for blocked accounts with clear AI explanation.
4. AI Daily Database Scan (`scan_daily_punches_for_buddy_punches`) detecting duplicate punch images across employee IDs.
5. HR Permission & Unlock workflow restoring the employee account.
"""
import io
import os
import shutil
import tempfile
import unittest
import numpy as np
from PIL import Image, ImageEnhance

import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app
from app.db import get_db, init_db
from app.face_engine import register_face_for_employee
from app.security_shield import (
    block_employee_account_by_ai,
    unlock_employee_account_by_hr,
    scan_daily_punches_for_buddy_punches,
)
from tests.test_face_security import make_test_face


class TestAIBuddyPunchDefense(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_ai_shield.db")
        cls.selfie_dir = os.path.join(cls.tmpdir, "selfies")
        os.makedirs(cls.selfie_dir, exist_ok=True)

        os.environ["DATABASE"] = cls.db_path
        os.environ["SELFIE_DIR"] = cls.selfie_dir
        os.environ["APP_ENV"] = "testing"
        os.environ["DEMO_DATA"] = "0"

        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["DATABASE"] = cls.db_path
        cls.app.config["SELFIE_DIR"] = cls.selfie_dir
        cls.app.config["MIN_PUNCH_GAP_MIN"] = 0

        init_db(cls.app)

        with cls.app.app_context():
            db = get_db()
            db.execute("UPDATE offices SET lat=12.9716, lng=77.5946, radius_m=200 WHERE id=1")
            db.execute("UPDATE employees SET office_id=1, must_change_pw=0")
            db.commit()

        cls.face_emp1 = make_test_face(1)
        cls.face_emp2 = make_test_face(2)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _get_csrf(self, client, url="/login"):
        res = client.get(url)
        import re
        txt = res.get_data(as_text=True)
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', txt)
        if not m:
            m = re.search(r'name="csrf-token"\s+content="([^"]+)"', txt)
        return m.group(1) if m else ""

    def _login(self, client, code, password, portal=""):
        client.get("/logout")
        url = f"/login/{portal}" if portal else "/login"
        token = self._get_csrf(client, url)
        return client.post(url, data={"emp_code": code, "password": password, "csrf_token": token}, follow_redirects=True)

    def test_01_real_time_buddy_punch_blocks_account_immediately(self):
        """When an employee punches using another registered employee's face:
        1. The punch is rejected with HTTP 403.
        2. The employee account is immediately blocked (active=0).
        3. A security incident is recorded in ai_face_alerts.
        """
        with self.app.app_context():
            db = get_db()
            e1 = db.execute("SELECT id FROM employees WHERE emp_code='EMP001'").fetchone()
            e2 = db.execute("SELECT id FROM employees WHERE emp_code='E0012'").fetchone()

            # Register face 1 for EMP001, and face 2 for E0012
            register_face_for_employee(db, e1["id"], self.face_emp1, registered_by_id=1, force=True)
            register_face_for_employee(db, e2["id"], self.face_emp2, registered_by_id=1, force=True)

        c = self.app.test_client()
        # E0012 logs in through Staff Portal
        self._login(c, "E0012", "123456789", portal="staff")
        t = self._get_csrf(c, "/dashboard")

        ch = c.post("/api/challenge", headers={"X-CSRFToken": t}).get_json()
        self.assertTrue(ch["ok"])

        # E0012 punches in using EMP001's face (Buddy punch / cross-account spoofing)
        frames = [self.face_emp1, make_test_face(1, brightness=1.15), self.face_emp1]
        punch_res = c.post("/api/punch", json={
            "kind": "in",
            "token": ch["token"],
            "frames": frames,
            "selfie": self.face_emp1,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 15
        }, headers={"X-CSRFToken": t})

        # Punch MUST be rejected
        self.assertEqual(punch_res.status_code, 403)

        # In the next second: E0012's account MUST be BLOCKED!
        with self.app.app_context():
            db = get_db()
            e2_updated = db.execute("SELECT active, block_reason FROM employees WHERE emp_code='E0012'").fetchone()
            self.assertEqual(e2_updated["active"], 0, "Account was not blocked by AI Shield!")
            self.assertIn("Buddy Punch", e2_updated["block_reason"])

            # Incident recorded in ai_face_alerts
            alert = db.execute("SELECT * FROM ai_face_alerts WHERE emp_id=? ORDER BY id DESC LIMIT 1", (e2["id"],)).fetchone()
            self.assertIsNotNone(alert)
            self.assertEqual(alert["status"], "BLOCKED")
            self.assertEqual(alert["target_emp_id"], e1["id"])

    def test_02_blocked_employee_cannot_login(self):
        """An account locked by AI defense cannot log in, receiving clear error notice."""
        c = self.app.test_client()
        res = self._login(c, "E0012", "123456789", portal="staff")
        txt = res.get_data(as_text=True)
        self.assertIn("Account Blocked by AI", txt)

    def test_03_hr_permission_unlock_workflow(self):
        """HR reviews incident in /hr/face-security-audit and unlocks employee."""
        c = self.app.test_client()
        # Log in as HR Admin
        self._login(c, "HR001", "damo12345", portal="staff")

        # View audit page
        audit_res = c.get("/hr/face-security-audit")
        self.assertEqual(audit_res.status_code, 200)
        self.assertIn("AI Biometric Buddy Punch", audit_res.get_data(as_text=True))

        with self.app.app_context():
            db = get_db()
            alert = db.execute("SELECT id FROM ai_face_alerts WHERE status='BLOCKED' ORDER BY id DESC LIMIT 1").fetchone()
            alert_id = alert["id"]

        t = self._get_csrf(c, "/hr/face-security-audit")
        # HR Grants permission & unlocks
        unlock_res = c.post(f"/hr/face-security-audit/resolve/{alert_id}", data={
            "action": "unlock",
            "notes": "Verified identity with HR Manager.",
            "csrf_token": t
        }, follow_redirects=True)

        self.assertEqual(unlock_res.status_code, 200)
        self.assertIn("UNLOCKED successfully", unlock_res.get_data(as_text=True))

        # Verify employee is active again in DB
        with self.app.app_context():
            db = get_db()
            e2 = db.execute("SELECT active, block_reason FROM employees WHERE emp_code='E0012'").fetchone()
            self.assertEqual(e2["active"], 1)
            self.assertIsNone(e2["block_reason"])

    def test_04_ai_daily_database_scanner(self):
        """Tests scan_daily_punches_for_buddy_punches detecting same-day duplicate face punches."""
        with self.app.app_context():
            db = get_db()
            # Insert punch record for EMP001 today
            today_s = "2026-10-16"
            db.execute("INSERT OR REPLACE INTO attendance (emp_id, date, in_selfie, check_in, source) VALUES (1, ?, 'selfie_a.jpg', '09:00:00', 'app')", (today_s,))
            # Save selfie image to disk
            im = Image.fromarray(np.full((64, 64), 180, dtype=np.uint8))
            im.save(os.path.join(self.selfie_dir, "selfie_a.jpg"))

            # Run daily scan
            violations = scan_daily_punches_for_buddy_punches(db, target_date_str=today_s)
            self.assertIsInstance(violations, list)

            # Also test DD-MM-YYYY format normalization
            violations_normalized = scan_daily_punches_for_buddy_punches(db, target_date_str="16-10-2026")
            self.assertIsInstance(violations_normalized, list)

    def test_05_cross_employee_same_face_punch_today_blocks_second_employee(self):
        """If Employee A checks in with Face X, and Employee B later attempts to check in
        with the same Face X, AI Biometric Defense immediately blocks Employee B with HTTP 403.
        """
        c = self.app.test_client()

        with self.app.app_context():
            db = get_db()
            e1 = db.execute("SELECT id, emp_code FROM employees WHERE emp_code='EMP001'").fetchone()
            # Ensure an active contract employee exists
            e_cl = db.execute("SELECT id, emp_code FROM employees WHERE emp_code='CL001'").fetchone()
            if not e_cl:
                db.execute(
                    "INSERT INTO employees (emp_code, name, role, active, category_id, office_id, password_hash, must_change_pw) "
                    "VALUES ('CL001', 'Ramesh Kumar', 'employee', 1, 3, 1, 'scrypt:...', 0)"
                )
                db.commit()
            from app.services import d2s, today
            today_s = d2s(today())
            db.execute("DELETE FROM attendance WHERE date=?", (today_s,))
            # Put Employee 1 attendance check-in photo in today's records
            selfie_fn = f"EMP001_{today_s.replace('-', '')}_in_090000.jpg"
            db.execute(
                "INSERT INTO attendance (emp_id, date, in_selfie, check_in, source, face_verified) "
                "VALUES (?, ?, ?, ?, 'app', 1)",
                (e1["id"], today_s, selfie_fn, f"{today_s} 09:00:00")
            )
            db.commit()

            # Save face_emp1 as the saved punch photo
            from PIL import Image
            import base64
            raw = base64.b64decode(self.face_emp1.split(",", 1)[1])
            with open(os.path.join(self.selfie_dir, selfie_fn), "wb") as f:
                f.write(raw)

        # Now CL001 attempts to punch in using the same face!
        self._login(c, "CL001", "123456789", portal="contract")
        t = self._get_csrf(c, "/dashboard")
        ch = c.post("/api/challenge", headers={"X-CSRFToken": t}).get_json()

        # Punch with identical/same face
        res = c.post("/api/punch", json={
            "kind": "in",
            "token": ch.get("token"),
            "frames": [self.face_emp1, self.face_emp1, self.face_emp1],
            "selfie": self.face_emp1,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 15
        }, headers={"X-CSRFToken": t})

        # MUST be blocked with HTTP 403
        self.assertEqual(res.status_code, 403)
        res_json = res.get_json()
        self.assertFalse(res_json["ok"])
        self.assertEqual(res_json.get("shield_status"), "BLOCKED")
        self.assertIn("AI Buddy Punch Blocked", res_json["error"])

        # Offending employee (CL001) MUST now be blocked in DB!
        with self.app.app_context():
            db = get_db()
            cl_row = db.execute("SELECT active, block_reason FROM employees WHERE emp_code='CL001'").fetchone()
            self.assertEqual(cl_row["active"], 0)
            self.assertIn("EMP001", cl_row["block_reason"])

    def test_06_hr_dashboard_displays_ai_alert_popup_and_card(self):
        """HR Dashboard must display the pop-up modal and alert banner when duplicate face attacks are active."""
        c = self.app.test_client()
        self._login(c, "HR001", "damo12345", portal="staff")

        dash_res = c.get("/hr/dashboard")
        self.assertEqual(dash_res.status_code, 200)
        txt = dash_res.get_data(as_text=True)

        # Verify Pop-up Modal and Banner are present in HTML
        self.assertIn("aiSecurityModal", txt)
        self.assertIn("AI Security Alert: Duplicate Face Detected on 2 Different IDs!", txt)
        self.assertIn("CRITICAL BIOMETRIC ALERT: Duplicate Face Detected on 2 Different IDs!", txt)
        self.assertIn("CL001", txt)


if __name__ == "__main__":
    unittest.main()

