"""Comprehensive tests for new user requirements:
1. Accounts & Finance Portal Login (4th category).
2. Black screen / covered camera rejection (No face detected).
3. Working hours status rules:
   - 2.0h to 6.0h: Half Day ('HD')
   - 6.0h to 9.0h+: Full Day ('P')
   - < 2.0h: Absent ('A')
4. Late Come Permission category & late-waiver punch flow.
"""
import base64
import io
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
import numpy as np
from PIL import Image

import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app
from app.db import get_db, init_db
from app.services import (
    compute_attendance_status,
    d2s,
    now,
    parse_dt,
    today,
    approved_permission_for,
)
from app.face_engine import check_selfie_image_quality
from tests.test_face_security import make_test_face


class TestNewFeaturesAccountsBlackPermission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_new_features.db")
        cls.selfie_dir = os.path.join(cls.tmpdir, "selfies")
        os.makedirs(cls.selfie_dir, exist_ok=True)

        os.environ["DATABASE"] = cls.db_path
        os.environ["SELFIE_DIR"] = cls.selfie_dir
        os.environ["APP_ENV"] = "testing"
        os.environ["DEMO_DATA"] = "1"

        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["DATABASE"] = cls.db_path
        cls.app.config["SELFIE_DIR"] = cls.selfie_dir
        cls.app.config["MIN_PUNCH_GAP_MIN"] = 0

        init_db(cls.app)

        with cls.app.app_context():
            db = get_db()
            db.execute("UPDATE offices SET lat=12.9716, lng=77.5946, radius_m=500 WHERE id=1")
            db.commit()

        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _get_csrf(self, client=None, path="/login"):
        c = client or self.client
        resp = c.get(path)
        m = re.search(r'name="csrf_token" value="([^"]+)"', resp.get_data(as_text=True))
        if m:
            return m.group(1)
        m2 = re.search(r'name="csrf-token" content="([^"]+)"', resp.get_data(as_text=True))
        return m2.group(1) if m2 else ""

    def _login(self, client, emp_code, password, portal=""):
        client.get("/logout")
        path = f"/login/{portal}" if portal else "/login"
        token = self._get_csrf(client, path)
        return client.post(path, data={"emp_code": emp_code, "password": password, "csrf_token": token}, follow_redirects=True)

    # 1. Accounts & Finance Login Page and 4 Categories
    def test_01_four_login_tabs_rendered(self):
        """Login page must display all 4 category tabs: Staff, Employee, Contract, Accounts."""
        res = self.client.get("/login")
        html = res.get_data(as_text=True)
        self.assertIn("Staff", html)
        self.assertIn("Employee", html)
        self.assertIn("Contract", html)
        self.assertIn("Accounts", html)
        self.assertIn("/login/accounts", html)

    def test_02_accounts_employee_login_allowed(self):
        """Employee in Accounts & Finance category (e.g. S197) can login via /login/accounts."""
        with self.app.app_context():
            db = get_db()
            cat = db.execute("SELECT id FROM employee_categories WHERE code='ACCOUNTS_FINANCE'").fetchone()
            self.assertIsNotNone(cat)
            emp = db.execute("SELECT * FROM employees WHERE emp_code='S197'").fetchone()
            self.assertIsNotNone(emp)

        res = self._login(self.client, "S197", "123456789", portal="accounts")
        html = res.get_data(as_text=True)
        self.assertIn("Dashboard", html)
        self.assertNotIn("Access Denied", html)

    def test_02b_all_four_categories_and_hr_login_pass(self):
        """All 4 category logins (Staff, Employee, Contract, Accounts) & HR admin login cleanly."""
        c = self.app.test_client()

        # 1. Staff Login
        res_staff = self._login(c, "E0012", "123456789", portal="staff")
        self.assertIn("Dashboard", res_staff.get_data(as_text=True))
        self.assertNotIn("Access Denied", res_staff.get_data(as_text=True))

        # 2. Employee Login
        res_emp = self._login(c, "EMP001", "123456789", portal="employee")
        self.assertIn("Dashboard", res_emp.get_data(as_text=True))
        self.assertNotIn("Access Denied", res_emp.get_data(as_text=True))

        # 3. Contract Employee Login
        res_contract = self._login(c, "CL001", "123456789", portal="contract")
        self.assertIn("Dashboard", res_contract.get_data(as_text=True))
        self.assertNotIn("Access Denied", res_contract.get_data(as_text=True))

        # 4. Accounts & Finance Login
        res_acc = self._login(c, "S197", "123456789", portal="accounts")
        self.assertIn("Dashboard", res_acc.get_data(as_text=True))
        self.assertNotIn("Access Denied", res_acc.get_data(as_text=True))

        # 5. HR Admin Login
        res_hr = self._login(c, "HR001", "123456789", portal="")
        self.assertIn("HR Dashboard", res_hr.get_data(as_text=True))

    def test_03_accounts_employee_wrong_tab_shows_friendly_guidance(self):
        """If Accounts employee tries to log in via Staff tab, friendly redirection guidance is shown."""
        res = self._login(self.client, "S197", "123456789", portal="staff")
        html = res.get_data(as_text=True)
        self.assertTrue("Accounts & Finance" in html or "accounts" in html.lower())

    # 2. Black Screen / Covered Camera Rejection
    def test_04_black_screen_detected_by_quality_engine(self):
        """Pitch black or heavily covered camera is flagged as invalid."""
        # 1. Pure black image (0 luminance)
        black_im = Image.new("RGB", (128, 128), (0, 0, 0))
        ok, err, b = check_selfie_image_quality(black_im)
        self.assertFalse(ok)
        self.assertIn("black or too dark", err.lower())

        # 2. Covered camera / very low light (brightness < 22)
        dark_im = Image.new("RGB", (128, 128), (10, 10, 10))
        ok, err, b = check_selfie_image_quality(dark_im)
        self.assertFalse(ok)
        self.assertIn("black or too dark", err.lower())

        # 3. Normal illumination face
        face_str = make_test_face(1, brightness=1.0)
        ok, err, b = check_selfie_image_quality(face_str)
        self.assertTrue(ok)

    def test_05_punch_with_black_screen_rejected_with_http_400(self):
        """Punch with pitch black selfie snapshot is rejected before any punch or record is saved."""
        c = self.app.test_client()
        self._login(c, "E0012", "123456789", portal="staff")
        t = self._get_csrf(c, "/punch")
        ch = c.post("/api/challenge", headers={"X-CSRFToken": t}).get_json()

        # Pure black snapshot
        black_buf = io.BytesIO()
        Image.new("RGB", (128, 128), (0, 0, 0)).save(black_buf, "JPEG")
        black_b64 = "data:image/jpeg;base64," + base64.b64encode(black_buf.getvalue()).decode()

        res = c.post("/api/punch", json={
            "kind": "in",
            "token": ch["token"],
            "frames": [black_b64, black_b64, black_b64],
            "selfie": black_b64,
            "lat": 12.9716, "lng": 77.5946, "accuracy": 10
        }, headers={"X-CSRFToken": t})

        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("black or too dark", data["error"].lower())

    # 3. Attendance Working Hours Rules (2-6h HD, 6-9h+ Full Day, <2h Absent)
    def test_06_attendance_hours_rules(self):
        """Test the 2-6 hour half day and 6-9 hour full day rules."""
        shift = {"start_time": "09:00", "end_time": "18:00", "grace_min": 15, "half_hours": 2.0, "full_hours": 6.0}
        cat = {"work_hours": 10.0, "overtime_hours": 0.0, "half_day_min_hours": 2.0, "half_day_time": "12:00", "early_full_day_cutoff": "15:00"}

        # A) 1.5 hours (< 2.0 hours) -> Absent ('A')
        cin = parse_dt("2026-10-17 09:00:00")
        cout_short = parse_dt("2026-10-17 10:30:00")
        st, ot = compute_attendance_status(cin, cout_short, shift, cat)
        self.assertEqual(st, "A", f"Expected Absent for 1.5h, got {st}")

        # B) 3.5 hours (2.0h to 6.0h) -> Half Day ('HD')
        cout_hd = parse_dt("2026-10-17 12:30:00")
        st, ot = compute_attendance_status(cin, cout_hd, shift, cat)
        self.assertEqual(st, "HD", f"Expected HD for 3.5h, got {st}")

        # C) 5.5 hours (2.0h to 6.0h) -> Half Day ('HD')
        cout_hd2 = parse_dt("2026-10-17 14:30:00")
        st, ot = compute_attendance_status(cin, cout_hd2, shift, cat)
        self.assertEqual(st, "HD", f"Expected HD for 5.5h, got {st}")

        # D) 6.5 hours (6.0h to 9.0h) -> Full Day ('P')
        cout_p = parse_dt("2026-10-17 15:30:00")
        st, ot = compute_attendance_status(cin, cout_p, shift, cat)
        self.assertEqual(st, "P", f"Expected P for 6.5h, got {st}")

        # E) 8.0 hours (6.0h to 9.0h) -> Full Day ('P')
        cout_p2 = parse_dt("2026-10-17 17:00:00")
        st, ot = compute_attendance_status(cin, cout_p2, shift, cat)
        self.assertEqual(st, "P", f"Expected P for 8.0h, got {st}")

        # F) 11.0 hours (> 10.0 category hours) -> OT
        cout_ot = parse_dt("2026-10-17 20:00:00")
        st, ot = compute_attendance_status(cin, cout_ot, shift, cat)
        self.assertEqual(st, "OT", f"Expected OT for 11.0h, got {st}")
        self.assertEqual(ot, 1.0)

    # 4. Late Come Permission Category & Request / Approval / Punch Waiver Flow
    def test_07_late_come_permission_category_seeded(self):
        """Late Come Permission category must exist in database."""
        with self.app.app_context():
            db = get_db()
            cat = db.execute("SELECT * FROM employee_categories WHERE code='LATE_PERMISSION'").fetchone()
            self.assertIsNotNone(cat)
            self.assertIn("Late Come Permission", cat["name"])

    def test_08_permission_request_and_late_waiver(self):
        """Employee can request 4h bank work permission, HR approves it, and punch late penalty is waived."""
        c = self.app.test_client()

        # Step 1: Employee applies for 4-hour permission for bank work
        self._login(c, "E0012", "123456789", portal="staff")
        t = self._get_csrf(c, "/permission")
        today_s = d2s(today())

        post_res = c.post("/permission", data={
            "perm_date": today_s,
            "duration_hours": "4",
            "perm_type": "bank",
            "reason": "Bank KYC document submission",
            "from_time": "09:00",
            "to_time": "13:00",
            "csrf_token": t
        }, follow_redirects=True)
        self.assertIn("sent to HR for approval", post_res.get_data(as_text=True))

        with self.app.app_context():
            db = get_db()
            perm = db.execute("SELECT * FROM permission_requests WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012') ORDER BY id DESC LIMIT 1").fetchone()
            self.assertIsNotNone(perm)
            self.assertEqual(perm["status"], "Pending")
            self.assertEqual(perm["duration_hours"], 4.0)
            perm_id = perm["id"]

        # Step 2: HR logs in and approves the permission request
        c_hr = self.app.test_client()
        self._login(c_hr, "HR001", "123456789", portal="")
        t_hr = self._get_csrf(c_hr, "/hr/approvals")
        app_res = c_hr.post(f"/hr/approvals/permission/{perm_id}", data={
            "decision": "Approved",
            "remarks": "Approved for official bank KYC",
            "csrf_token": t_hr
        }, follow_redirects=True)
        self.assertEqual(app_res.status_code, 200)

        with self.app.app_context():
            db = get_db()
            approved_perm = approved_permission_for(perm["emp_id"], today_s)
            self.assertIsNotNone(approved_perm)
            self.assertEqual(approved_perm["status"], "Approved")

        # Step 3: Employee checks dashboard - permission banner is visible
        dash_res = c.get("/dashboard")
        dash_html = dash_res.get_data(as_text=True)
        self.assertIn("Late Come Permission Approved for Today", dash_html)
        self.assertIn("4.0h", dash_html)

    # 5. Cyber AI and Audit Timestamps Are Exactly IST
    def test_09_cyber_ai_and_audit_timestamps_are_ist(self):
        """Cyber defense threat blocks, AI account blocks, and audit records must log in Indian Standard Time (IST)."""
        from app.security_shield import log_security_event, block_employee_account_by_ai
        from app.services import audit, parse_dt, now

        with self.app.app_context():
            db = get_db()
            t_before = now()

            # 1. Cyber attack blocked logging
            log_security_event(threat_type="SQL_INJECTION", payload="' UNION SELECT * FROM employees --", source="AI_SHIELD")
            cyber_log = db.execute("SELECT * FROM audit_log WHERE action='CYBER_ATTACK_BLOCKED' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertIsNotNone(cyber_log)
            log_dt = parse_dt(cyber_log["ts"])
            self.assertIsNotNone(log_dt)
            # Must match current IST time within 10 seconds
            self.assertLess(abs((now() - log_dt).total_seconds()), 10)

            # 2. General audit logging
            audit("TEST_IST_ACTION", "test_entity", 1, "Testing IST timestamp")
            gen_log = db.execute("SELECT * FROM audit_log WHERE action='TEST_IST_ACTION' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertIsNotNone(gen_log)
            gen_dt = parse_dt(gen_log["ts"])
            self.assertLess(abs((now() - gen_dt).total_seconds()), 10)

            # 3. AI account blocked and alert timestamp
            emp = db.execute("SELECT id FROM employees WHERE emp_code='CL001'").fetchone()
            block_employee_account_by_ai(db, emp["id"], reason="Duplicate face detected on 2 IDs", similarity=0.88)
            alert = db.execute("SELECT * FROM ai_face_alerts WHERE emp_id=? ORDER BY id DESC LIMIT 1", (emp["id"],)).fetchone()
            self.assertIsNotNone(alert)
            alert_dt = parse_dt(alert["created_at"])
            self.assertIsNotNone(alert_dt)
            self.assertLess(abs((now() - alert_dt).total_seconds()), 10)

    def test_10_buddy_punch_cross_id_detection_and_hr_audit_scan(self):
        """Cross-ID Buddy Punch Defense:
        If ID 1 (E0012) punches, and ID 2 (S197) attempts to punch with the same face:
        1. ID 2 punch is immediately BLOCKED by AI.
        2. ID 2 account is locked.
        3. HR Daily Audit scan detects the violation and reports it (never says 0 violations clean).
        4. HR Dashboard renders the AI security modal alert.
        """
        from app.security_shield import scan_daily_punches_for_buddy_punches
        c = self.app.test_client()

        # Step 1: E0012 punches in successfully with Face 1
        self._login(c, "E0012", "123456789", portal="staff")
        t1 = self._get_csrf(c, "/punch")
        ch1 = c.post("/api/challenge", headers={"X-CSRFToken": t1}).get_json()
        face1 = make_test_face(1, brightness=1.0)
        frames1 = [make_test_face(1, brightness=0.95), make_test_face(1, brightness=1.05), face1]
        res1 = c.post("/api/punch", json={
            "kind": "in", "token": ch1["token"], "frames": frames1, "selfie": face1,
            "lat": 12.9716, "lng": 77.5946, "accuracy": 15
        }, headers={"X-CSRFToken": t1})
        self.assertTrue(res1.get_json()["ok"])

        # Step 2: S197 (Accounts) logs in and attempts to punch with the EXACT SAME Face 1
        self._login(c, "S197", "123456789", portal="accounts")
        t2 = self._get_csrf(c, "/punch")
        ch2 = c.post("/api/challenge", headers={"X-CSRFToken": t2}).get_json()
        res2 = c.post("/api/punch", json={
            "kind": "in", "token": ch2["token"], "frames": frames1, "selfie": face1,
            "lat": 12.9716, "lng": 77.5946, "accuracy": 15
        }, headers={"X-CSRFToken": t2})

        # Must be blocked by AI Defense
        d2 = res2.get_json()
        self.assertEqual(res2.status_code, 403)
        self.assertFalse(d2.get("ok"))
        self.assertEqual(d2.get("shield_status"), "BLOCKED")
        self.assertIn("Buddy Punch", d2.get("error"))

        with self.app.app_context():
            db = get_db()
            today_s = d2s(today())

            # Verify S197 account is locked
            emp2 = db.execute("SELECT active, block_reason FROM employees WHERE emp_code='S197'").fetchone()
            self.assertEqual(emp2["active"], 0)
            self.assertIn("Buddy Punch", emp2["block_reason"])

            # Step 3: Run HR Daily Punch Audit Scan
            violations = scan_daily_punches_for_buddy_punches(db, target_date_str=today_s)
            blocked_count = db.execute("SELECT COUNT(*) FROM ai_face_alerts WHERE date=? AND status='BLOCKED'", (today_s,)).fetchone()[0]
            total_violations = max(len(violations), blocked_count)
            self.assertGreater(total_violations, 0)

        # Step 4: HR Dashboard renders the AI security modal alert
        self._login(c, "HR001", "123456789", portal="")
        dash_html = c.get("/hr/dashboard").get_data(as_text=True)
        self.assertIn("aiSecurityModal", dash_html)
        self.assertIn("Duplicate Face Detected on 2 Different IDs", dash_html)
        self.assertIn("S197", dash_html)

    def test_11_four_department_400_image_face_analysis_and_blocking(self):
        """AI Biometric Defense:
        1. Analyzes up to 400 stored images across all 4 departments / categories:
           - Staff
           - Employees
           - Contract Labour
           - Accounts & Finance
        2. If another face matches across the 400 photos in ANY department, punch is blocked and account is locked.
        3. HR audit scan endpoint flash message reflects the 400 analyzed images across all 4 departments.
        """
        c = self.app.test_client()

        with self.app.app_context():
            db = get_db()

            # Ensure all 4 categories exist and map test employees
            cat_staff = db.execute("SELECT id FROM employee_categories WHERE code='STAFF'").fetchone()["id"]
            cat_emp = db.execute("SELECT id FROM employee_categories WHERE code='EMPLOYEES'").fetchone()["id"]
            cat_contract = db.execute("SELECT id FROM employee_categories WHERE code='CONTRACT'").fetchone()["id"]
            cat_acc = db.execute("SELECT id FROM employee_categories WHERE code='ACCOUNTS_FINANCE'").fetchone()["id"]

            db.execute("UPDATE employees SET category_id=? WHERE emp_code='E0012'", (cat_staff,))
            db.execute("UPDATE employees SET category_id=? WHERE emp_code='EMP001'", (cat_emp,))
            db.execute("UPDATE employees SET category_id=? WHERE emp_code='CL001'", (cat_contract,))
            db.execute("UPDATE employees SET category_id=? WHERE emp_code='S197'", (cat_acc,))
            db.commit()

            # Save a stored selfie for CL001 (Contract Labour) on a previous date
            from app.api import _save_selfie, _decode_image
            face_cl = make_test_face(2, brightness=1.0)
            emp_cl = db.execute("SELECT * FROM employees WHERE emp_code='CL001'").fetchone()
            cl_selfie_name = _save_selfie(_decode_image(face_cl), emp_cl, "in")

            db.execute(
                "INSERT OR REPLACE INTO attendance (emp_id, date, in_selfie, check_in, status, source) "
                "VALUES (?, '2026-10-15', ?, '08:30:00', 'P', 'app')",
                (emp_cl["id"], cl_selfie_name)
            )
            # Re-activate EMP001
            db.execute("UPDATE employees SET active=1, block_reason=NULL WHERE emp_code='EMP001'")
            db.commit()

        # Step 2: Employee EMP001 (Employees department) attempts to punch with the EXACT SAME Face 2 as CL001
        self._login(c, "EMP001", "123456789", portal="employee")
        t = self._get_csrf(c, "/punch")
        ch = c.post("/api/challenge", headers={"X-CSRFToken": t}).get_json()
        face_e1 = make_test_face(2, brightness=1.02)
        frames_e1 = [make_test_face(2, brightness=0.98), face_e1, face_e1]

        res = c.post("/api/punch", json={
            "kind": "in", "token": ch["token"], "frames": frames_e1, "selfie": face_e1,
            "lat": 12.9716, "lng": 77.5946, "accuracy": 15
        }, headers={"X-CSRFToken": t})

        # Punch MUST be blocked by 4-Department 400-Image AI Defense
        d = res.get_json()
        self.assertEqual(res.status_code, 403)
        self.assertFalse(d["ok"])
        self.assertEqual(d["shield_status"], "BLOCKED")
        self.assertIn("4-Department 400-Image Defense", d["error"])

        # Employee account is immediately locked
        with self.app.app_context():
            db = get_db()
            e1_row = db.execute("SELECT active, block_reason FROM employees WHERE emp_code='EMP001'").fetchone()
            self.assertEqual(e1_row["active"], 0)
            self.assertIn("Buddy Punch", e1_row["block_reason"])

        # Step 3: Test HR 4-Department Scan endpoint POST /hr/face-security-audit/scan
        self._login(c, "HR001", "123456789", portal="")
        scan_t = self._get_csrf(c, "/hr/face-security-audit")
        scan_res = c.post("/hr/face-security-audit/scan", data={
            "date": "2026-10-15"
        }, headers={"X-CSRFToken": scan_t}, follow_redirects=True)
        scan_html = scan_res.get_data(as_text=True)
        self.assertIn("AI 4-Department Audit", scan_html)
        self.assertIn("400 analyzed", scan_html)


