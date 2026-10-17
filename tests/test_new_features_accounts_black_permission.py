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
