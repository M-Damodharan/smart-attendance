"""Test Suite for HR Shift Management, 3-Tier Login Architecture, and Face Change Approval Workflow.
Verifies:
1. 3 Separate Login Types (Staff, Employee, Contract Labour) with role and category RBAC.
2. Bulk Employee Selection and Shift Assignment (Permanent & Weekly Rotation).
3. Employee Face Change Request, Password Verification & HR Approval/Reset Workflow.
4. HR Dashboard Workforce Tiers & Shift Metrics.
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.db import get_db, init_db
from app.services import d2s, get_employee_shift_for_date, today


class TestHRShiftsAndFaceChange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_shifts.db")
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

    def _login(self, client, code, password, login_type="employee"):
        client.get("/logout")
        t = self._get_csrf(client, f"/login/{login_type}")
        return client.post(
            f"/login/{login_type}",
            data={"emp_code": code, "password": password, "csrf_token": t, "login_type": login_type},
            follow_redirects=True
        )

    # =========================================================================
    # 1. THREE SEPARATE LOGIN TYPES & PERMISSIONS
    # =========================================================================
    def test_01_three_login_types_enforcement(self):
        """Staff, Employee, and Contract Labour logins must strictly permit only their respective accounts."""
        c = self.app.test_client()

        # E0012 is Staff -> Staff login succeeds
        res_staff = self._login(c, "E0012", "123456789", login_type="staff")
        self.assertEqual(res_staff.status_code, 200)
        self.assertIn(b"Logout", res_staff.data)

        # EMP001 is Employee Staff -> Attempting Staff login is rejected with 403
        c.get("/logout")
        t = self._get_csrf(c, "/login/staff")
        res_emp_on_staff = c.post("/login/staff", data={
            "emp_code": "EMP001", "password": "123456789", "csrf_token": t, "login_type": "staff"
        })
        self.assertEqual(res_emp_on_staff.status_code, 403)
        self.assertIn(b"Employee Staff", res_emp_on_staff.data)

        # CL001 is Contract Labour -> Attempting Staff login is rejected with 403
        c.get("/logout")
        t = self._get_csrf(c, "/login/staff")
        res_cl_on_staff = c.post("/login/staff", data={
            "emp_code": "CL001", "password": "123456789", "csrf_token": t, "login_type": "staff"
        })
        self.assertEqual(res_cl_on_staff.status_code, 403)
        self.assertIn(b"Contract Labour", res_cl_on_staff.data)

        # CL001 logging into Contract Labour login succeeds
        res_cl = self._login(c, "CL001", "123456789", login_type="contract")
        self.assertEqual(res_cl.status_code, 200)
        self.assertIn(b"Logout", res_cl.data)

        # EMP001 logging into Employee portal succeeds
        res_emp = self._login(c, "EMP001", "123456789", login_type="employee")
        self.assertEqual(res_emp.status_code, 200)
        self.assertIn(b"Logout", res_emp.data)

    # =========================================================================
    # 2. HR BULK SHIFT MANAGEMENT & WEEKLY ROTATION
    # =========================================================================
    def test_02_hr_bulk_shift_assignment_permanent(self):
        """HR can select multiple employees and assign Morning Shift in bulk."""
        c = self.app.test_client()
        self._login(c, "HR001", "damo12345", login_type="staff")

        with self.app.app_context():
            db = get_db()
            e1 = db.execute("SELECT id FROM employees WHERE emp_code='EMP001'").fetchone()["id"]
            e2 = db.execute("SELECT id FROM employees WHERE emp_code='CL001'").fetchone()["id"]
            morning_shift = db.execute("SELECT id FROM shifts WHERE name LIKE '%Morning%'").fetchone()["id"]

        t = self._get_csrf(c, "/hr/shifts")
        res = c.post("/hr/shifts/bulk-assign", data={
            "csrf_token": t,
            "emp_ids": [str(e1), str(e2)],
            "shift_id": str(morning_shift),
            "assignment_type": "permanent"
        }, follow_redirects=True)

        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Successfully updated default shift", res.data)

        with self.app.app_context():
            db = get_db()
            emp1 = db.execute("SELECT shift_id FROM employees WHERE id=?", (e1,)).fetchone()
            emp2 = db.execute("SELECT shift_id FROM employees WHERE id=?", (e2,)).fetchone()
            self.assertEqual(emp1["shift_id"], morning_shift)
            self.assertEqual(emp2["shift_id"], morning_shift)

    def test_03_hr_bulk_weekly_shift_rotation(self):
        """HR can bulk schedule next week's shift with effective date range."""
        c = self.app.test_client()
        self._login(c, "HR001", "damo12345", login_type="staff")

        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT id FROM employees WHERE emp_code='EMP001'").fetchone()
            eid = emp["id"]
            evening_shift = db.execute("SELECT id FROM shifts WHERE name LIKE '%Evening%'").fetchone()["id"]

        start_date = "2026-11-09"
        end_date = "2026-11-15"
        t = self._get_csrf(c, "/hr/shifts")
        res = c.post("/hr/shifts/bulk-assign", data={
            "csrf_token": t,
            "emp_ids": [str(eid)],
            "shift_id": str(evening_shift),
            "assignment_type": "weekly",
            "start_date": start_date,
            "end_date": end_date,
            "notes": "Week 46 Evening Rotation"
        }, follow_redirects=True)

        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Successfully scheduled weekly rotation", res.data)

        with self.app.app_context():
            db = get_db()
            # On 2026-11-11 (inside schedule), shift must be evening_shift
            eff_shift = get_employee_shift_for_date(eid, "2026-11-11", fallback_shift_id=1)
            self.assertEqual(eff_shift["id"], evening_shift)

            # On 2026-11-20 (outside schedule), shift must return to default
            outside_shift = get_employee_shift_for_date(eid, "2026-11-20", fallback_shift_id=1)
            self.assertNotEqual(outside_shift["id"], evening_shift)

    # =========================================================================
    # 3. FACE CHANGE REQUEST & HR APPROVAL WORKFLOW
    # =========================================================================
    def test_04_face_change_request_and_hr_approval_workflow(self):
        """Employee submits face-change with password -> HR approves -> face template reset for re-registration."""
        c_emp = self.app.test_client()
        # Login as EMP001
        self._login(c_emp, "EMP001", "123456789", login_type="employee")

        # 1. Bad password rejected
        t_req = self._get_csrf(c_emp, "/face-change")
        res_bad_pw = c_emp.post("/face-change", data={
            "csrf_token": t_req,
            "password": "WrongPassword",
            "reason": "Changed my beard and glasses"
        }, follow_redirects=True)
        self.assertIn(b"Identity verification failed", res_bad_pw.data)

        # 2. Valid password submits request
        res_ok = c_emp.post("/face-change", data={
            "csrf_token": t_req,
            "password": "123456789",
            "reason": "Changed appearance significantly; need new photo"
        }, follow_redirects=True)
        self.assertIn(b"Face change request submitted successfully", res_ok.data)

        with self.app.app_context():
            db = get_db()
            req = db.execute("SELECT * FROM face_change_requests WHERE status='Pending' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertIsNotNone(req)
            self.assertEqual(req["reason"], "Changed appearance significantly; need new photo")
            rid = req["id"]

        # 3. HR reviews and approves in /hr/approvals
        c_hr = self.app.test_client()
        self._login(c_hr, "HR001", "damo12345", login_type="staff")

        res_appr_page = c_hr.get("/hr/approvals")
        self.assertEqual(res_appr_page.status_code, 200)
        self.assertIn(b"Face ID Change Requests", res_appr_page.data)

        t_decide = self._get_csrf(c_hr, "/hr/approvals")
        res_decide = c_hr.post(f"/hr/approvals/face/{rid}", data={
            "csrf_token": t_decide,
            "decision": "Approved",
            "remarks": "Verified in person; approved for new photo."
        }, follow_redirects=True)
        self.assertIn(b"Face change request approved", res_decide.data)

        with self.app.app_context():
            db = get_db()
            decided_req = db.execute("SELECT * FROM face_change_requests WHERE id=?", (rid,)).fetchone()
            self.assertEqual(decided_req["status"], "Approved")

            # Check that employee's face_registered is reset to 0 so they can register new face
            emp = db.execute("SELECT face_registered FROM employees WHERE emp_code='EMP001'").fetchone()
            self.assertEqual(emp["face_registered"], 0)

            # Audit log records approval
            audit_log = db.execute("SELECT * FROM audit_log WHERE action='FACE_CHANGE_APPROVED'").fetchone()
            self.assertIsNotNone(audit_log)

    # =========================================================================
    # 4. HR DASHBOARD WORKFORCE & SECURITY METRICS
    # =========================================================================
    def test_05_hr_dashboard_metrics(self):
        """HR dashboard accurately displays Staff, Employees, and Contract Labour counts."""
        c = self.app.test_client()
        self._login(c, "HR001", "damo12345", login_type="staff")

        res = c.get("/hr/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Workforce &amp; Biometric Security Overview", res.data)
        self.assertIn(b"Staff Portal", res.data)
        self.assertIn(b"Employees (Rotating)", res.data)
        self.assertIn(b"Contract Labour", res.data)
        self.assertIn(b"Bulk Shifts", res.data)


if __name__ == "__main__":
    unittest.main()
