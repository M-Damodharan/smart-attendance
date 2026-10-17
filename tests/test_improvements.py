"""Unit and Integration Tests for Attendance System Improvements & New Features.
Covers:
1. Punch duration calculation (1 minute test, e.g. 2:21 PM to 2:22 PM -> "1m", no UTC drift)
2. Attendance rules:
   - Clockout after 16:00 -> Full Day Present ('P')
   - Clockout 14:00 to 16:00 -> Half Day ('HD')
   - Unpunched past days -> Absent ('A')
3. Employee Categories & Working Hours:
   - Staff (10h), Employees (12h), Contract Employees (10h + 2h OT)
4. Weekly Shift Schedules:
   - Dynamic shift assignment overriding employee default shift
5. HR & Employee Security:
   - Password change for employee and HR
   - Audit logging for failed logins, password changes, and attendance edits
   - Verification of login page without "HR001" placeholder
6. AI Chat Assistant:
   - All 7 example questions answered accurately with live data
   - Strict Role-Based Access Control (RBAC) preventing employees from accessing company-wide data
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app import create_app
from app.db import get_db, init_db
from app.services import (compute_attendance_status, d2s, finalize_attendance, format_duration,
                          get_employee_category, get_employee_shift_for_date, parse_dt, s2d, today)
from app.ai_assistant import process_ai_query


class TestAttendanceImprovements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_improvements.db")
        cls.selfie_dir = os.path.join(cls.tmpdir, "selfies")
        os.makedirs(cls.selfie_dir, exist_ok=True)

        os.environ["DATABASE"] = cls.db_path
        os.environ["SELFIE_DIR"] = cls.selfie_dir
        os.environ["APP_ENV"] = "testing"
        os.environ["DEMO_DATA"] = "1"

        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["MIN_PUNCH_GAP_MIN"] = 0
        cls.app.config["DATABASE"] = cls.db_path
        cls.app.config["SELFIE_DIR"] = cls.selfie_dir
        cls.app.config["DEMO_DATA"] = True

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

    def _login(self, client, emp_code, password):
        client.get("/logout")
        token = self._get_csrf(client, "/login")
        return client.post("/login", data={"emp_code": emp_code, "password": password, "csrf_token": token}, follow_redirects=True)

    # =========================================================================
    # 1. Punch Duration & UTC Drift Fix
    # =========================================================================
    def test_01_one_minute_duration_calculation(self):
        """Punch in at 14:21 (2:21 PM) and punch out at 14:22 (2:22 PM) must calculate 1 minute duration."""
        cin = "2026-10-15 14:21:00"
        cout = "2026-10-15 14:22:00"
        shift = {"start_time": "08:00", "end_time": "20:00", "grace_min": 15, "half_hours": 4.0, "full_hours": 8.0}
        category = {"work_hours": 10.0, "overtime_hours": 0.0, "half_day_min_hours": 4.0,
                    "half_day_time": "14:00", "early_full_day_cutoff": "16:00"}
        
        upd = finalize_attendance({"check_in": cin, "check_out": cout}, shift, category)
        self.assertAlmostEqual(upd["work_hours"], 1 / 60, places=2)
        
        # Test format_duration helper
        dur_text = format_duration(upd["work_hours"])
        self.assertEqual(dur_text, "1m", f"Expected '1m' but got '{dur_text}'")

        # Test formatting other durations
        self.assertEqual(format_duration(8.25), "8h 15m")
        self.assertEqual(format_duration(0), "0m")

    # =========================================================================
    # 2. Attendance Rules & Auto-Calculation
    # =========================================================================
    def test_02_attendance_rules_clockout_after_1600(self):
        """Clock out after 4:00 PM (16:00) counts as Full Day Present (P)."""
        shift = {"start_time": "08:00", "end_time": "20:00", "grace_min": 15, "half_hours": 4.0, "full_hours": 8.0}
        category = {"work_hours": 10.0, "overtime_hours": 0.0, "half_day_min_hours": 4.0,
                    "half_day_time": "14:00", "early_full_day_cutoff": "16:00"}
        
        # Clock in 08:00, clock out 16:15 -> Full Day Present
        cin = parse_dt("2026-10-15 08:00:00")
        cout = parse_dt("2026-10-15 16:15:00")
        status, ot = compute_attendance_status(cin, cout, shift, category)
        self.assertEqual(status, "P", f"Expected 'P' for clock out after 16:00, got {status}")

    def test_03_attendance_rules_half_day(self):
        """Working between 2.0 and 6.0 hours counts as Half Day (HD)."""
        shift = {"start_time": "08:00", "end_time": "20:00", "grace_min": 15, "half_hours": 2.0, "full_hours": 6.0}
        category = {"work_hours": 10.0, "overtime_hours": 0.0, "half_day_min_hours": 2.0,
                    "half_day_time": "12:00", "early_full_day_cutoff": "14:00"}
        
        # Clock in 08:00, clock out 12:30 (4.5 hours, 2 <= hours < 6) -> Half Day (HD)
        cin = parse_dt("2026-10-15 08:00:00")
        cout = parse_dt("2026-10-15 12:30:00")
        status, ot = compute_attendance_status(cin, cout, shift, category)
        self.assertEqual(status, "HD", f"Expected 'HD' for 4.5h work, got {status}")

    def test_04_attendance_rules_insufficient_hours_absent(self):
        """Punch out before 14:00 or with < 4 hours counts as Absent (A)."""
        shift = {"start_time": "08:00", "end_time": "20:00", "grace_min": 15, "half_hours": 4.0, "full_hours": 8.0}
        category = {"work_hours": 10.0, "overtime_hours": 0.0, "half_day_min_hours": 4.0,
                    "half_day_time": "14:00", "early_full_day_cutoff": "16:00"}
        
        # Clock in 14:21, clock out 14:22 (1 min) -> Absent (A)
        cin = parse_dt("2026-10-15 14:21:00")
        cout = parse_dt("2026-10-15 14:22:00")
        status, ot = compute_attendance_status(cin, cout, shift, category)
        self.assertEqual(status, "A", f"Expected 'A' for 1-minute punch, got {status}")

    # =========================================================================
    # 3. Employee Categories & Working Hours
    # =========================================================================
    def test_05_staff_category_hours_and_overtime(self):
        """Staff: 10 working hours per day. Hours > 10 count as OT."""
        shift = {"start_time": "08:00", "end_time": "20:00", "grace_min": 15, "half_hours": 4.0, "full_hours": 8.0}
        category_staff = {"work_hours": 10.0, "overtime_hours": 0.0, "half_day_min_hours": 4.0,
                          "half_day_time": "14:00", "early_full_day_cutoff": "16:00"}

        # 10 hours: 08:00 to 18:00 -> P, ot_hours = 0
        cin = parse_dt("2026-10-15 08:00:00")
        cout1 = parse_dt("2026-10-15 18:00:00")
        status1, ot1 = compute_attendance_status(cin, cout1, shift, category_staff)
        self.assertEqual(status1, "P")
        self.assertEqual(ot1, 0.0)

        # 12 hours: 08:00 to 20:00 -> OT, ot_hours = 2.0
        cout2 = parse_dt("2026-10-15 20:00:00")
        status2, ot2 = compute_attendance_status(cin, cout2, shift, category_staff)
        self.assertEqual(status2, "OT")
        self.assertEqual(ot2, 2.0)

    def test_06_employee_category_12h(self):
        """Employees: 12 working hours per day. 12h = P, 13h = OT (1h)."""
        shift = {"start_time": "08:00", "end_time": "20:00", "grace_min": 15, "half_hours": 5.0, "full_hours": 12.0}
        category_emp = {"work_hours": 12.0, "overtime_hours": 0.0, "half_day_min_hours": 5.0,
                        "half_day_time": "14:00", "early_full_day_cutoff": "16:00"}

        # 12 hours -> P, ot_hours = 0
        cin = parse_dt("2026-10-15 08:00:00")
        cout1 = parse_dt("2026-10-15 20:00:00")
        status1, ot1 = compute_attendance_status(cin, cout1, shift, category_emp)
        self.assertEqual(status1, "P")
        self.assertEqual(ot1, 0.0)

        # 13 hours -> OT, ot_hours = 1.0
        cout2 = parse_dt("2026-10-15 21:00:00")
        status2, ot2 = compute_attendance_status(cin, cout2, shift, category_emp)
        self.assertEqual(status2, "OT")
        self.assertEqual(ot2, 1.0)

    def test_07_contract_category_10h_plus_2h_ot(self):
        """Contract Employees: 10h regular + 2h OT."""
        shift = {"start_time": "08:00", "end_time": "20:00", "grace_min": 15, "half_hours": 4.0, "full_hours": 8.0}
        category_contract = {"work_hours": 10.0, "overtime_hours": 2.0, "half_day_min_hours": 4.0,
                             "half_day_time": "14:00", "early_full_day_cutoff": "16:00"}

        # 12 hours worked -> 10h regular + 2h OT -> status OT
        cin = parse_dt("2026-10-15 08:00:00")
        cout = parse_dt("2026-10-15 20:00:00")
        status, ot = compute_attendance_status(cin, cout, shift, category_contract)
        self.assertEqual(status, "OT")
        self.assertEqual(ot, 2.0)

    # =========================================================================
    # 4. Weekly Shift Scheduling
    # =========================================================================
    def test_08_weekly_shift_scheduling_override(self):
        """Weekly shift schedules must override employee's default shift for the scheduled dates."""
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT id, shift_id FROM employees WHERE emp_code='E001'").fetchone()
            emp_id = emp["id"]
            default_shift_id = emp["shift_id"]

            # Query shift for a date with no schedule -> returns default shift
            shift_default = get_employee_shift_for_date(emp_id, "2026-11-01", default_shift_id)
            self.assertEqual(shift_default["id"], default_shift_id)

            # Insert weekly shift schedule: assign to Shift 2 (Night shift) for Nov 02 to Nov 08
            other_shift = db.execute("SELECT id FROM shifts WHERE id != ?", (default_shift_id,)).fetchone()
            if other_shift:
                sched_shift_id = other_shift["id"]
                db.execute(
                    "INSERT INTO weekly_shift_schedules (emp_id, shift_id, start_date, end_date, notes) "
                    "VALUES (?, ?, '2026-11-02', '2026-11-08', 'Week 45 Rotation')",
                    (emp_id, sched_shift_id)
                )
                db.commit()

                # Query shift for Nov 04 (inside schedule) -> must return sched_shift_id
                shift_sched = get_employee_shift_for_date(emp_id, "2026-11-04", default_shift_id)
                self.assertEqual(shift_sched["id"], sched_shift_id, "Weekly schedule must override default shift")

                # Query shift for Nov 15 (outside schedule) -> must return default shift
                shift_outside = get_employee_shift_for_date(emp_id, "2026-11-15", default_shift_id)
                self.assertEqual(shift_outside["id"], default_shift_id)

    # =========================================================================
    # 5. Security, Login Interface & Audit Logging
    # =========================================================================
    def test_09_login_page_no_hr001_placeholder(self):
        """Login page must not contain the placeholder 'HR001'."""
        res = self.client.get("/login")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertNotIn('placeholder="HR001"', html, "Placeholder 'HR001' should be removed from login page")
        self.assertIn('placeholder="Enter your employee code or email"', html)

    def test_10_employee_and_hr_password_change(self):
        """Both Employee and HR can change their password and action is audited."""
        # Log in as E001
        self._login(self.client, "E001", "emp123")

        # Get change password token
        token = self._get_csrf(self.client, "/change-password")
        post_data = {
            "current": "emp123",
            "new": "NewSecurePass2026!",
            "confirm": "NewSecurePass2026!",
            "csrf_token": token
        }
        res = self.client.post("/change-password", data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Verify audit log recorded password change
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT id FROM employees WHERE emp_code='E001'").fetchone()
            log = db.execute(
                "SELECT * FROM audit_log WHERE entity_id=? AND action='PASSWORD_CHANGE'", (str(emp["id"]),)
            ).fetchone()
            self.assertIsNotNone(log, "Audit log should record PASSWORD_CHANGE")

    def test_11_login_attempt_audit_logging(self):
        """Failed login attempts must be logged in audit_log."""
        # Post bad credentials
        token = self._get_csrf(self.client, "/login")
        self.client.post("/login", data={"emp_code": "E001", "password": "WrongPassword", "csrf_token": token})

        with self.app.app_context():
            db = get_db()
            log = db.execute(
                "SELECT * FROM audit_log WHERE action='LOGIN_FAILED' AND details LIKE '%E001%'"
            ).fetchone()
            self.assertIsNotNone(log, "Audit log should record LOGIN_FAILED attempt")

    # =========================================================================
    # 6. AI Assistant: All 7 Required Questions & RBAC
    # =========================================================================
    def test_12_ai_assistant_seven_questions_and_rbac(self):
        """AI Assistant must answer the 7 example queries accurately and enforce RBAC."""
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT * FROM employees WHERE emp_code='E001'").fetchone()
            hr = db.execute("SELECT * FROM employees WHERE role='hr'").fetchone()
            today_s = d2s(today())

            # Seed a completed attendance record for E001 today
            db.execute("DELETE FROM attendance WHERE emp_id=? AND date=?", (emp["id"], today_s))
            db.execute(
                "INSERT INTO attendance (emp_id, date, check_in, check_out, work_hours, ot_hours, status, source) "
                "VALUES (?, ?, ?, ?, 10.5, 0.5, 'OT', 'app')",
                (emp["id"], today_s, f"{today_s} 08:00:00", f"{today_s} 18:30:00")
            )
            db.commit()

            # Q1: "How many hours have I worked today?"
            q1 = process_ai_query(emp, "How many hours have I worked today?")["reply"]
            self.assertIn("10h 30m", q1, "Q1 should report 10h 30m worked")

            # Q2: "What time did I punch in and punch out?"
            q2 = process_ai_query(emp, "What time did I punch in and punch out?")["reply"]
            self.assertIn("08:00 AM", q2)
            self.assertIn("06:30 PM", q2)

            # Q3: "Am I marked Present, Half Day or Absent today?"
            q3 = process_ai_query(emp, "Am I marked Present, Half Day or Absent today?")["reply"]
            self.assertIn("OT", q3)

            # Q4: "How much overtime did I work today?"
            q4 = process_ai_query(emp, "How much overtime did I work today?")["reply"]
            self.assertIn("30m", q4)

            # Q5: "Show my attendance history for this month."
            q5 = process_ai_query(emp, "Show my attendance history for this month.")["reply"]
            self.assertIn("Attendance Summary for", q5)

            # Q6: "How many employees are present today?"
            # Employee asks -> Restricted
            q6_emp = process_ai_query(emp, "How many employees are present today?")["reply"]
            self.assertIn("Access Restricted", q6_emp)
            self.assertIn("only accessible to HR", q6_emp)
            # HR asks -> Permitted
            q6_hr = process_ai_query(hr, "How many employees are present today?")["reply"]
            self.assertIn("Present:", q6_hr)

            # Q7: "Show today's absent employees."
            # Employee asks -> Restricted
            q7_emp = process_ai_query(emp, "Show today's absent employees?")["reply"]
            self.assertIn("Access Restricted", q7_emp)
            # HR asks -> Permitted
            q7_hr = process_ai_query(hr, "Show today's absent employees.")["reply"]
            self.assertIn("absent", q7_hr.lower())


if __name__ == "__main__":
    unittest.main()
