import os
import shutil
import tempfile
import unittest
from datetime import date
from werkzeug.security import check_password_hash

from app import create_app
from app.db import get_db, init_db


class TestEmployeeCRUD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_employee_crud.db")
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

        init_db(cls.app)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _login_hr(self):
        with self.client.session_transaction() as sess:
            sess["uid"] = 1
            sess["role"] = "hr"
            sess["name"] = "HR Admin"

    def test_01_create_employee_full_fields(self):
        """HR creates an employee with all fields specified."""
        self._login_hr()
        
        # Get category, shift, office ids
        with self.app.app_context():
            db = get_db()
            s_id = db.execute("SELECT id FROM shifts LIMIT 1").fetchone()[0]
            o_id = db.execute("SELECT id FROM offices LIMIT 1").fetchone()[0]
            c_id = db.execute("SELECT id FROM employee_categories WHERE code='EMPLOYEES'").fetchone()[0]

        res = self.client.post("/hr/employees/new", data={
            "emp_code": "EMPTEST01",
            "name": "Full Test Employee",
            "email": "test01@example.com",
            "phone": "9876543210",
            "department": "Engineering",
            "designation": "Developer",
            "role": "employee",
            "shift_id": s_id,
            "office_id": o_id,
            "category_id": c_id,
            "password": "CustomSecret123!",
            "active": "1"
        }, follow_redirects=True)
        
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Employee created", res.data)
        self.assertNotIn(b"Internal Server Error", res.data)

        with self.app.app_context():
            db = get_db()
            row = db.execute("SELECT * FROM employees WHERE emp_code='EMPTEST01'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["name"], "Full Test Employee")
            self.assertEqual(row["department"], "Engineering")
            self.assertEqual(row["shift_id"], s_id)
            self.assertEqual(row["office_id"], o_id)
            self.assertEqual(row["category_id"], c_id)
            self.assertTrue(check_password_hash(row["password_hash"], "CustomSecret123!"))
            self.assertEqual(row["must_change_pw"], 1)

    def test_02_create_employee_minimal_fields_fallback(self):
        """HR creates an employee with minimal fields (no shift, office, or category provided)."""
        self._login_hr()

        res = self.client.post("/hr/employees/new", data={
            "emp_code": "EMPTEST02",
            "name": "Minimal Test Employee",
            "role": "employee",
            "active": "1"
        }, follow_redirects=True)

        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Employee created", res.data)
        self.assertNotIn(b"Internal Server Error", res.data)

        with self.app.app_context():
            db = get_db()
            row = db.execute("SELECT * FROM employees WHERE emp_code='EMPTEST02'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["name"], "Minimal Test Employee")
            self.assertIsNotNone(row["shift_id"])
            self.assertIsNotNone(row["office_id"])
            self.assertIsNotNone(row["category_id"])
            self.assertTrue(check_password_hash(row["password_hash"], "Welcome@123"))

    def test_03_create_duplicate_code_handled_gracefully(self):
        """Creating an employee with an existing code flashes an error instead of 500."""
        self._login_hr()

        res = self.client.post("/hr/employees/new", data={
            "emp_code": "EMPTEST01",
            "name": "Duplicate Code User",
            "role": "employee"
        }, follow_redirects=True)

        self.assertEqual(res.status_code, 200)
        self.assertIn(b"already exists", res.data)
        self.assertNotIn(b"Internal Server Error", res.data)

    def test_04_edit_and_delete_employee(self):
        """Editing and deleting employee works and cascade cleans up related records."""
        self._login_hr()

        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT id FROM employees WHERE emp_code='EMPTEST01'").fetchone()
            eid = emp["id"]
            # Add a schedule to verify cascade cleanup
            shift_id = db.execute("SELECT id FROM shifts LIMIT 1").fetchone()[0]
            db.execute("INSERT INTO weekly_shift_schedules (emp_id, shift_id, start_date, end_date) VALUES (?, ?, '2026-10-01', '2026-10-31')",
                       (eid, shift_id))
            db.commit()

        # Edit employee
        res_edit = self.client.post(f"/hr/employees/{eid}", data={
            "emp_code": "EMPTEST01",
            "name": "Updated Test Employee",
            "department": "QA",
            "designation": "Lead",
            "role": "employee",
            "active": "1"
        }, follow_redirects=True)

        self.assertEqual(res_edit.status_code, 200)
        self.assertIn(b"Employee updated successfully", res_edit.data)

        # Delete employee
        res_del = self.client.post(f"/hr/employees/{eid}/delete", data={"confirm": "EMPTEST01"}, follow_redirects=True)
        self.assertEqual(res_del.status_code, 200)
        self.assertIn(b"deleted permanently", res_del.data)

        with self.app.app_context():
            db = get_db()
            deleted_emp = db.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone()
            self.assertIsNone(deleted_emp)
            schedules = db.execute("SELECT * FROM weekly_shift_schedules WHERE emp_id=?", (eid,)).fetchall()
            self.assertEqual(len(schedules), 0)

    def test_05_newly_created_employee_can_login(self):
        """A newly created employee can log in with temporary password."""
        self.client.get("/logout")
        res = self.client.post("/login", data={
            "emp_code": "EMPTEST02",
            "password": "Welcome@123"
        }, follow_redirects=True)

        self.assertEqual(res.status_code, 200)
        # must_change_pw is 1, so redirected to change password
        self.assertTrue(b"change password" in res.data.lower() or b"please set a new password" in res.data.lower())
