"""Test Suite for HR Dashboard Logic, Shift Resolution, Late Punch Calculation, and 4-Department Workforce.
Validates:
1. HR Dashboard late arrivals calculation (stats['late'] accurately reflects late check-ins, never stuck at 0).
2. Shift resolution preserves employee assigned shifts (e.g. General Shift, Staff) and does not hijack them into Shift 1.
3. 4-Department Workforce Overview and Working Rules (Staff, Employees, Contract, Accounts & Finance).
4. Daily Attendance Register groups employees by their assigned/scheduled shift.
5. Employee User Dashboard displays assigned shift, category rules, and late badge when checked in late.
6. All HR and Employee pages render cleanly with HTTP 200 OK.
"""
import io
import os
import sys
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.db import get_db
from app.services import d2s, today, resolve_employee_shift, is_late, parse_dt


class TestDashboardShiftAndLateLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.app.app_context():
            db = get_db()
            t = d2s(today())
            # Clean up test attendance records for today
            db.execute("DELETE FROM attendance WHERE date=?", (t,))
            db.commit()

    def _login_hr(self):
        with self.app.app_context():
            db = get_db()
            hr_user = db.execute("SELECT id FROM employees WHERE role='hr' LIMIT 1").fetchone()
            uid = hr_user["id"] if hr_user else 1
        with self.client.session_transaction() as sess:
            sess["uid"] = uid
            sess["role"] = "hr"
            sess["name"] = "HR Admin"

    def _login_emp(self, emp_id=None):
        with self.app.app_context():
            db = get_db()
            if emp_id:
                emp = db.execute("SELECT id, emp_code, name FROM employees WHERE id=?", (emp_id,)).fetchone()
            else:
                emp = db.execute("SELECT id, emp_code, name FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
            uid = emp["id"] if emp else 2
            emp_code = emp["emp_code"] if emp else "EMP001"
            emp_name = emp["name"] if emp else "Employee Staff"
        with self.client.session_transaction() as sess:
            sess["uid"] = uid
            sess["role"] = "employee"
            sess["emp_code"] = emp_code
            sess["name"] = emp_name

    def test_01_shift_resolution_preserves_assigned_shifts(self):
        """Assigned shifts (General Shift, Staff, Accounts) must not be hijacked into Shift 1."""
        with self.app.app_context():
            db = get_db()
            # Find or ensure General Shift
            gen_shift = db.execute("SELECT * FROM shifts WHERE name LIKE '%General%'").fetchone()
            if not gen_shift:
                cur = db.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, active) VALUES (?, ?, ?, ?, 1)",
                                 ("General Shift", "09:00", "18:00", 15))
                gen_shift = db.execute("SELECT * FROM shifts WHERE id=?", (cur.lastrowid,)).fetchone()

            # Find employee or create one assigned to General Shift
            emp = db.execute("SELECT * FROM employees WHERE shift_id=?", (gen_shift["id"],)).fetchone()
            if not emp:
                cur = db.execute("INSERT INTO employees (emp_code, name, role, shift_id, category_id, active, password_hash) VALUES (?, ?, 'employee', ?, 1, 1, 'dummy')",
                                 ("TESTGEN01", "General Shift Worker", gen_shift["id"]))
                emp = db.execute("SELECT * FROM employees WHERE id=?", (cur.lastrowid,)).fetchone()
                db.commit()

            # Test punch at 13:30 (middle of day)
            resolved = resolve_employee_shift(emp["id"], today(), "13:30", db)
            self.assertEqual(resolved["id"], gen_shift["id"])
            self.assertIn("General", resolved["name"])

    def test_02_rotating_employees_auto_switch_shift1_and_shift2(self):
        """Employees assigned to Shift 1 or 2 dynamically switch day/night shifts."""
        with self.app.app_context():
            db = get_db()
            s1 = db.execute("SELECT * FROM shifts WHERE name LIKE '%Shift 1%'").fetchone()
            s2 = db.execute("SELECT * FROM shifts WHERE name LIKE '%Shift 2%'").fetchone()
            if s1 and s2:
                # Employee assigned to Shift 1
                emp = db.execute("SELECT * FROM employees WHERE shift_id=?", (s1["id"],)).fetchone()
                if not emp:
                    cur = db.execute("INSERT INTO employees (emp_code, name, role, shift_id, category_id, active, password_hash) VALUES (?, ?, 'employee', ?, 2, 1, 'dummy')",
                                     ("TESTROT01", "Rotating Worker", s1["id"]))
                    emp = db.execute("SELECT * FROM employees WHERE id=?", (cur.lastrowid,)).fetchone()
                    db.commit()

                # Punch in morning (08:15) -> Shift 1
                res_morning = resolve_employee_shift(emp["id"], today(), "08:15", db)
                self.assertIn("Shift 1", res_morning["name"])

                # Punch in night (20:30) -> Shift 2
                res_night = resolve_employee_shift(emp["id"], today(), "20:30", db)
                self.assertIn("Shift 2", res_night["name"])

    def test_03_hr_dashboard_counts_late_arrivals_accurately(self):
        """stats['late'] on HR Dashboard must count late arrivals accurately, not remain at 0."""
        with self.app.app_context():
            db = get_db()
            t = d2s(today())
            emp = db.execute("SELECT * FROM employees WHERE role='employee' AND active=1").fetchone()
            self.assertIsNotNone(emp)

            # Insert attendance with late=1
            cin = f"{t} 13:30:00"
            db.execute("INSERT INTO attendance (emp_id, date, shift_id, check_in, late) VALUES (?, ?, ?, ?, 1)",
                       (emp["id"], t, emp["shift_id"], cin))
            db.commit()

        # Log in as HR and view dashboard
        self._login_hr()
        res = self.client.get("/hr/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Executive HR Dashboard", res.data)
        # Verify stats['late'] > 0 in HTML
        self.assertIn(b"<span>Late Arrivals</span>", res.data)
        self.assertNotIn(b"<b>0</b>\n    <span>Late Arrivals</span>", res.data)

    def test_04_hr_dashboard_displays_all_four_departments(self):
        """Dashboard must show Workforce Overview and Category Rules for all 4 departments."""
        self._login_hr()
        res = self.client.get("/hr/dashboard")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")

        # 4 Departments in Workforce Overview
        self.assertIn("Staff Portal", html)
        self.assertIn("Employees (Rotating)", html)
        self.assertIn("Contract Labour", html)
        self.assertIn("Accounts &amp; Finance", html)

        # 4 Departments in Active Category Working Rules
        self.assertIn("Staff Category", html)
        self.assertIn("Employees Category", html)
        self.assertIn("Contract Employees", html)
        self.assertIn("Accounts &amp; Finance", html)

    def test_05_daily_attendance_register_shift_grouping(self):
        """Attendance register groups employees by assigned shift and shows 4 department rules."""
        self._login_hr()
        res = self.client.get("/hr/attendance")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")

        self.assertIn("Daily Attendance Register", html)
        self.assertIn("Staff:</strong> 10h/day", html)
        self.assertIn("Employees:</strong> 12h/day", html)
        self.assertIn("Contract:</strong> 10h regular + 2h OT", html)
        self.assertIn("Accounts &amp; Finance:</strong> 10h/day", html)

    def test_06_employee_dashboard_displays_correct_shift_and_late_badge(self):
        """Employee dashboard shows correct shift details and displays Late badge when checked in late."""
        with self.app.app_context():
            db = get_db()
            t = d2s(today())
            emp = db.execute("SELECT * FROM employees WHERE role='employee' AND active=1").fetchone()

            # Record checked-in late punch (no checkout)
            cin = f"{t} 13:30:00"
            db.execute("INSERT INTO attendance (emp_id, date, shift_id, check_in, late) VALUES (?, ?, ?, ?, 1)",
                       (emp["id"], t, emp["shift_id"], cin))
            db.commit()

        # Log in as employee
        self._login_emp(emp["id"])
        res = self.client.get("/dashboard")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")

        self.assertIn("Checked in at", html)
        self.assertIn("Late", html)

    def test_07_all_hr_pages_render_without_errors(self):
        """All 11 HR pages must render with HTTP 200 OK."""
        self._login_hr()
        routes = [
            "/hr/dashboard",
            "/hr/employees",
            "/hr/employees/new",
            "/hr/attendance",
            "/hr/shifts",
            "/hr/approvals",
            "/hr/corrections",
            "/hr/audit",
            "/hr/reports",
            "/hr/settings",
            "/hr/face-security-audit",
        ]
        for r in routes:
            res = self.client.get(r)
            self.assertEqual(res.status_code, 200, f"Route {r} failed with status {res.status_code}")

    def test_08_all_employee_pages_render_without_errors(self):
        """All employee portal pages must render with HTTP 200 OK."""
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT * FROM employees WHERE role='employee' AND active=1").fetchone()

        self._login_emp(emp["id"])
        routes = [
            "/dashboard",
            "/punch",
            "/history",
            "/leave",
            "/od",
            "/permission",
            "/face-change",
        ]
        for r in routes:
            res = self.client.get(r)
            self.assertEqual(res.status_code, 200, f"Route {r} failed with status {res.status_code}")


if __name__ == "__main__":
    unittest.main()
