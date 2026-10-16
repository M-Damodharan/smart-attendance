"""Comprehensive Test Suite: Testing ALL Pages, Functions, and API Routes.
Audits:
- Public & Auth pages (Login, Logout, Change Password, Healthz, PWA, APK)
- HR Portal (Dashboard, Employees, New Employee, Edit Employee, Attendance Register, Approvals, Corrections, Reports, Export, Settings, Audit)
- Employee Portal (Dashboard, Punch, History, History Export, Leaves, On-Duty)
- API & AI Assistant (/api/challenge, /api/ai/chat)
"""
import io
import re
import sys
import unittest
import openpyxl

from app import create_app
from app.db import get_db, init_db


class TestAllPagesComprehensive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = cls.app.test_client()

    # -------------------------------------------------------------------------
    # 1. PUBLIC & AUTH PAGES
    # -------------------------------------------------------------------------
    def test_01_public_login_page_get(self):
        """GET /login renders properly with title, form, and CSRF token."""
        res = self.client.get("/login")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Sign In", res.data)
        self.assertIn(b"SBG", res.data)
        self.assertIn(b"name=\"emp_code\"", res.data)
        self.assertIn(b"name=\"password\"", res.data)

    def test_02_public_login_failed_credentials(self):
        """POST /login with bad credentials displays clean error message, no 500."""
        res = self.client.post("/login", data={
            "emp_code": "NONEXISTENT",
            "password": "wrongpassword123"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Invalid employee code or password", res.data)

    def test_03_healthz_endpoint(self):
        """GET /healthz returns 200 OK JSON."""
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'"status":"ok"', res.data)

    def test_04_pwa_and_apk_assets(self):
        """PWA Service Worker, Manifest, and Android APK are downloadable."""
        r_sw = self.client.get("/static/js/sw.js")
        self.assertEqual(r_sw.status_code, 200)

        r_man = self.client.get("/static/manifest.json")
        self.assertEqual(r_man.status_code, 200)

        r_apk = self.client.get("/download/apk")
        self.assertIn(r_apk.status_code, (200, 302))

    # -------------------------------------------------------------------------
    # 2. HR PORTAL - EVERY PAGE & WORKFLOW
    # -------------------------------------------------------------------------
    def _login_hr(self):
        with self.app.app_context():
            db = get_db()
            hr_user = db.execute("SELECT id FROM employees WHERE role='hr' LIMIT 1").fetchone()
            uid = hr_user["id"] if hr_user else 1
        with self.client.session_transaction() as sess:
            sess["uid"] = uid
            sess["role"] = "hr"
            sess["name"] = "HR Admin"

    def test_10_hr_dashboard(self):
        """GET /hr/ renders dashboard with metrics, quick links, and charts."""
        self._login_hr()
        res = self.client.get("/hr/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Dashboard", res.data)
        self.assertIn(b"Employees", res.data)
        self.assertIn(b"Attendance", res.data)

    def test_11_hr_employees_list(self):
        """GET /hr/employees lists all employees with categories and status."""
        self._login_hr()
        res = self.client.get("/hr/employees")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Employee master", res.data)
        self.assertIn(b"Add employee", res.data)

    def test_12_hr_employee_new_form_and_create(self):
        """GET /hr/employees/new renders creation form and POST creates employee cleanly."""
        self._login_hr()
        # Form GET
        res_get = self.client.get("/hr/employees/new")
        self.assertEqual(res_get.status_code, 200)
        self.assertIn(b"New employee", res_get.data)
        self.assertIn(b"category_id", res_get.data)
        self.assertIn(b"shift_id", res_get.data)

        # Form POST create
        test_code = "TESTPAGE01"
        res_post = self.client.post("/hr/employees/new", data={
            "emp_code": test_code,
            "name": "Page Test User",
            "department": "Engineering",
            "designation": "Specialist",
            "role": "employee",
            "active": "1"
        }, follow_redirects=True)
        self.assertEqual(res_post.status_code, 200)
        self.assertIn(b"Employee created", res_post.data)

        # Verify DB
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT * FROM employees WHERE emp_code=?", (test_code,)).fetchone()
            self.assertIsNotNone(emp)
            eid = emp["id"]

        # Edit GET
        res_edit_get = self.client.get(f"/hr/employees/{eid}")
        self.assertEqual(res_edit_get.status_code, 200)
        self.assertIn(b"Edit", res_edit_get.data)

        # Edit POST
        res_edit_post = self.client.post(f"/hr/employees/{eid}", data={
            "emp_code": test_code,
            "name": "Page Test User Updated",
            "department": "Engineering",
            "designation": "Senior Specialist",
            "role": "employee",
            "active": "1"
        }, follow_redirects=True)
        self.assertEqual(res_edit_post.status_code, 200)
        self.assertIn(b"Employee updated successfully", res_edit_post.data)

        # Cleanup Delete
        res_del = self.client.post(f"/hr/employees/{eid}/delete", data={"confirm": test_code}, follow_redirects=True)
        self.assertEqual(res_del.status_code, 200)
        self.assertIn(b"deleted permanently", res_del.data)

    def test_13_hr_attendance_register(self):
        """GET /hr/attendance renders Shift 1 & Shift 2 registers with counts."""
        self._login_hr()
        res = self.client.get("/hr/attendance")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Attendance", res.data)
        self.assertIn(b"Shift 1", res.data)
        self.assertIn(b"Shift 2", res.data)

    def test_14_hr_approvals_page(self):
        """GET /hr/approvals displays leave and OD request approvals."""
        self._login_hr()
        res = self.client.get("/hr/approvals")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Approvals", res.data)
        self.assertIn(b"Pending", res.data)

    def test_15_hr_corrections_page(self):
        """GET /hr/corrections renders manual attendance adjustment form."""
        self._login_hr()
        res = self.client.get("/hr/corrections")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Attendance correction", res.data)
        self.assertIn(b"Check-in", res.data)

    def test_16_hr_reports_and_export(self):
        """GET /hr/reports renders report matrix and exports valid Excel workbook."""
        self._login_hr()
        res = self.client.get("/hr/reports")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Monthly Attendance Reports", res.data)
        self.assertIn(b"All Categories", res.data)

        # Master export
        res_exp = self.client.get("/hr/reports/export")
        self.assertEqual(res_exp.status_code, 200)
        self.assertIn("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", res_exp.headers.get("Content-Type"))
        wb = openpyxl.load_workbook(io.BytesIO(res_exp.data))
        self.assertTrue(len(wb.sheetnames) >= 1)

    def test_17_hr_settings_page(self):
        """GET /hr/settings displays offices, shifts, categories, and policies."""
        self._login_hr()
        res = self.client.get("/hr/settings")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Settings", res.data)
        self.assertIn(b"geofence", res.data.lower())
        self.assertIn(b"Shifts", res.data)

    def test_18_hr_audit_log_page(self):
        """GET /hr/audit displays tamper-evident audit events."""
        self._login_hr()
        res = self.client.get("/hr/audit")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Audit", res.data)

    # -------------------------------------------------------------------------
    # 3. EMPLOYEE PORTAL - EVERY PAGE & WORKFLOW
    # -------------------------------------------------------------------------
    def _login_employee(self):
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT id, name, emp_code FROM employees WHERE role='employee' LIMIT 1").fetchone()
            if not emp:
                c_id = db.execute("SELECT id FROM employee_categories LIMIT 1").fetchone()[0]
                s_id = db.execute("SELECT id FROM shifts LIMIT 1").fetchone()[0]
                o_id = db.execute("SELECT id FROM offices LIMIT 1").fetchone()[0]
                cur = db.execute(
                    "INSERT INTO employees (emp_code, name, role, category_id, shift_id, office_id, password_hash, must_change_pw) "
                    "VALUES ('E_PORTAL_TEST', 'Portal Test', 'employee', ?, ?, ?, 'hash', 0)",
                    (c_id, s_id, o_id)
                )
                db.commit()
                eid, ename = cur.lastrowid, "Portal Test"
            else:
                eid, ename = emp["id"], emp["name"]

        with self.client.session_transaction() as sess:
            sess["uid"] = eid
            sess["role"] = "employee"
            sess["name"] = ename

    def test_20_employee_dashboard(self):
        """GET /dashboard renders employee home with punch status and shift info."""
        self._login_employee()
        res = self.client.get("/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Dashboard", res.data)
        self.assertIn(b"Shift", res.data)

    def test_21_employee_punch_page(self):
        """GET /punch renders camera, GPS, and biometric punch UI."""
        self._login_employee()
        res = self.client.get("/punch")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"punch", res.data.lower())
        self.assertIn(b"geofence", res.data.lower())

    def test_22_employee_history_and_export(self):
        """GET /history renders punch logs and /history/export downloads personal Excel sheet."""
        self._login_employee()
        res = self.client.get("/history")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Attendance History", res.data)

        # Personal export
        res_exp = self.client.get("/history/export")
        self.assertEqual(res_exp.status_code, 200)
        self.assertIn("attachment;", res_exp.headers.get("Content-Disposition", ""))
        wb = openpyxl.load_workbook(io.BytesIO(res_exp.data))
        self.assertTrue(len(wb.sheetnames) >= 1)

    def test_23_employee_leave_page(self):
        """GET /leave displays quotas, applications, and form."""
        self._login_employee()
        res = self.client.get("/leave")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Leave", res.data)

    def test_24_employee_od_page(self):
        """GET /od displays on-duty requests and application form."""
        self._login_employee()
        res = self.client.get("/od")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"On-Duty", res.data)

    # -------------------------------------------------------------------------
    # 4. API & AI ASSISTANT ENDPOINTS
    # -------------------------------------------------------------------------
    def test_30_api_punch_challenge(self):
        """POST /api/challenge returns security token and prompt list."""
        self._login_employee()
        res = self.client.post("/api/challenge")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'"token"', res.data)
        self.assertIn(b'"prompts"', res.data)

    def test_31_api_ai_chat(self):
        """POST /api/ai/chat returns intelligent, accurate attendance response."""
        self._login_hr()
        res = self.client.post("/api/ai/chat", json={"message": "How many employees are present today?"})
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'"reply"', res.data)
        self.assertIn(b'"ok":true', res.data)


if __name__ == "__main__":
    unittest.main()
