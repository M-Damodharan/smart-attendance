import os
import unittest
from app import create_app
from app.db import get_db

class TestAuditExistingFeatures(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def test_01_public_pages(self):
        urls = ["/", "/login", "/login/staff", "/login/employee", "/login/contract", "/login/accounts"]
        for u in urls:
            res = self.client.get(u)
            self.assertIn(res.status_code, (200, 302), f"URL {u} returned {res.status_code}")

    def test_02_hr_pages(self):
        with self.app.app_context():
            db = get_db()
            hr_user = db.execute("SELECT * FROM employees WHERE role='hr' LIMIT 1").fetchone()
            db.execute("UPDATE employees SET must_change_pw=0 WHERE id=?", (hr_user["id"],))
            db.commit()

        with self.client.session_transaction() as sess:
            sess["uid"] = hr_user["id"]

        hr_urls = [
            "/hr/dashboard",
            "/hr/employees",
            "/hr/employees/new",
            "/hr/attendance",
            "/hr/shifts",
            "/hr/approvals",
            "/hr/corrections",
            "/hr/missed-punches",
            "/hr/face-security-audit",
            "/hr/reports",
            "/hr/audit",
            "/hr/settings",
            "/hr/cloud-backup-sync",
        ]
        for u in hr_urls:
            res = self.client.get(u)
            self.assertEqual(res.status_code, 200, f"HR URL {u} returned {res.status_code}")

    def test_03_employee_pages(self):
        with self.app.app_context():
            db = get_db()
            emp_user = db.execute("SELECT * FROM employees WHERE role='employee' LIMIT 1").fetchone()
            db.execute("UPDATE employees SET must_change_pw=0 WHERE id=?", (emp_user["id"],))
            db.commit()

        with self.client.session_transaction() as sess:
            sess["uid"] = emp_user["id"]

        emp_urls = [
            "/dashboard",
            "/punch",
            "/history",
            "/leave",
            "/od",
            "/permission",
            "/face-change",
        ]
        for u in emp_urls:
            res = self.client.get(u)
            self.assertEqual(res.status_code, 200, f"Employee URL {u} returned {res.status_code}")

if __name__ == "__main__":
    unittest.main()
