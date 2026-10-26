import json
import os
import shutil
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app import create_app
from app.db import get_db, init_db
from app.ai_assistant import process_ai_query


class TestRedesignFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_redesign.db")
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

    def test_01_persistent_session_configuration(self):
        """Permanent session lifetime must be set to 30 days (2,592,000 seconds)."""
        self.assertEqual(self.app.config["PERMANENT_SESSION_LIFETIME"], 30 * 24 * 3600)

    def test_02_account_switching_security_and_flow(self):
        """HR admin can switch to an employee account and switch back to HR; employee cannot switch without authorization."""
        # 1. Fresh employee cannot switch to another account
        self._login(self.client, "E001", "emp123")
        with self.app.app_context():
            db = get_db()
            emp2 = db.execute("SELECT * FROM employees WHERE emp_code='E0012'").fetchone()

        res_emp = self.client.post("/switch-account", json={"target_emp_id": emp2["id"]})
        self.assertEqual(res_emp.status_code, 403)

        # 2. Log in as HR
        self._login(self.client, "HR001", "damo12345")

        with self.app.app_context():
            db = get_db()
            emp1 = db.execute("SELECT * FROM employees WHERE emp_code='E001'").fetchone()

        # Switch to E001 (using JSON request)
        res = self.client.post("/switch-account", json={"target_emp_id": emp1["id"]})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("dashboard", data["redirect"])

        # Switch back to HR
        res_back = self.client.post("/switch-account", json={"target_emp_id": "hr_return"})
        self.assertEqual(res_back.status_code, 200)
        data_back = res_back.get_json()
        self.assertTrue(data_back["ok"])

    def test_03_profile_contact_update(self):
        """User can update their emergency contact and phone number."""
        self._login(self.client, "E001", "emp123")

        res = self.client.post("/update-profile", json={
            "phone": "9876543210",
            "emergency_contact": "9123456780"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])

        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT phone, emergency_contact FROM employees WHERE emp_code='E001'").fetchone()
            self.assertEqual(emp["phone"], "9876543210")

    def test_04_comments_api_create_and_fetch(self):
        """Comments API stores comments and retrieves them in chronological order."""
        self._login(self.client, "E001", "emp123")

        # Post a comment for attendance log 0 (general daily remark)
        res = self.client.post("/api/comments", json={
            "entity_type": "attendance",
            "entity_id": 0,
            "comment": "Field duty handover complete. All machine logs verified."
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("comment", data)
        self.assertEqual(data["comment"]["author_code"], "E001")

        # Fetch comments
        res_get = self.client.get("/api/comments/attendance/0")
        self.assertEqual(res_get.status_code, 200)
        data_get = res_get.get_json()
        self.assertTrue(data_get["ok"])
        self.assertGreaterEqual(len(data_get["comments"]), 1)
        self.assertEqual(data_get["comments"][-1]["content"], "Field duty handover complete. All machine logs verified.")

    def test_05_ai_assistant_absent_count_and_pending_od(self):
        """AI Assistant supports 'absent today' and 'pending OD approvals' queries."""
        with self.app.app_context():
            db = get_db()
            hr = db.execute("SELECT * FROM employees WHERE role='hr'").fetchone()
            emp = db.execute("SELECT * FROM employees WHERE emp_code='E001'").fetchone()

            # HR asking absent count
            resp_hr_absent = process_ai_query(hr, "How many employees are absent today?")
            self.assertIn("absent", resp_hr_absent["reply"].lower())

            # HR asking pending OD
            resp_hr_od = process_ai_query(hr, "Show pending OD approvals.")
            self.assertIn("od", resp_hr_od["reply"].lower())

            # Employee asking absent count (should be blocked by RBAC)
            resp_emp = process_ai_query(emp, "How many employees are absent today?")
            self.assertEqual(resp_emp.get("type"), "restricted")
            self.assertIn("restricted", resp_emp["reply"].lower())

    def test_06_request_and_approval_notifications(self):
        """User requests notify HR, and HR approval/rejection notifies the requesting user."""
        # 1. Employee E001 applies for OD
        self._login(self.client, "E001", "emp123")
        res_od = self.client.post("/od", data={
            "od_date": "2026-10-27",
            "location": "Client Plant A",
            "purpose": "Machine Calibration"
        }, follow_redirects=True)
        self.assertEqual(res_od.status_code, 200)

        # Verify HR received notification for the new request
        self._login(self.client, "HR001", "damo12345")
        res_hr_notif = self.client.get("/api/notifications")
        self.assertEqual(res_hr_notif.status_code, 200)
        data_hr = res_hr_notif.get_json()
        self.assertTrue(data_hr["ok"])
        self.assertGreaterEqual(data_hr["unread_count"], 1)
        found_od_notif = any("OD" in n["title"] or "On-Duty" in n["title"] for n in data_hr["notifications"])
        self.assertTrue(found_od_notif, "HR should receive OD request notification")

        # 2. HR approves the OD request
        with self.app.app_context():
            db = get_db()
            od_row = db.execute("SELECT id FROM od_requests WHERE location='Client Plant A' ORDER BY id DESC").fetchone()
            self.assertIsNotNone(od_row)

        res_decide = self.client.post(f"/hr/approvals/od/{od_row['id']}", data={
            "decision": "Approved",
            "remarks": "Approved for field calibration"
        }, follow_redirects=True)
        self.assertEqual(res_decide.status_code, 200)

        # 3. Employee E001 receives the approval notification
        self._login(self.client, "E001", "emp123")
        res_emp_notif = self.client.get("/api/notifications")
        self.assertEqual(res_emp_notif.status_code, 200)
        data_emp = res_emp_notif.get_json()
        self.assertTrue(data_emp["ok"])
        self.assertGreaterEqual(data_emp["unread_count"], 1)

        approved_notif = next((n for n in data_emp["notifications"] if "Approved" in n["title"] and n["kind"] == "od"), None)
        self.assertIsNotNone(approved_notif, "Employee should receive OD Approved notification")
        self.assertIn("approved by hr", approved_notif["message"].lower())

        # 4. Mark notification as read
        res_read = self.client.post(f"/api/notifications/read/{approved_notif['id']}")
        self.assertEqual(res_read.status_code, 200)

        # Verify read status
        res_after = self.client.get("/api/notifications")
        data_after = res_after.get_json()
        self.assertEqual(data_after["unread_count"], 0)


if __name__ == "__main__":
    unittest.main()
