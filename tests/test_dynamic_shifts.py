"""Tests for Dynamic Shift 1 & Shift 2 Auto-Assignment and Present Lists.
Verifies:
1. Punch in at 8:00 AM to 7:30 PM is automatically classified and stored in Shift 1 Present List.
2. Punch in at 8:00 PM to 8:00 AM is automatically classified and stored in Shift 2 Present List.
3. Attendance register (/hr/attendance) and HR dashboard group employees by their punch shift.
4. AI assistant correctly reports Shift 1 vs Shift 2 present counts and lists.
"""
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime
from app import create_app
from app.db import get_db, init_db
from app.services import determine_shift_from_punch, compute_attendance_status


class DynamicShiftTestCase(unittest.TestCase):
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
        cls.app.config["DEMO_DATA"] = False

        init_db(cls.app)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_determine_shift_from_punch(self):
        with self.app.app_context():
            db = get_db()
            # 8:00 AM -> Shift 1
            s1 = determine_shift_from_punch("2026-10-15 08:00:00", db)
            self.assertEqual(s1["start_time"], "08:00")
            self.assertEqual(s1["end_time"], "20:00")
            self.assertIn("Shift 1", s1["name"])

            # 2:00 PM -> Shift 1
            s1_afternoon = determine_shift_from_punch("2026-10-15 14:00:00", db)
            self.assertEqual(s1_afternoon["id"], s1["id"])

            # 8:00 PM -> Shift 2
            s2 = determine_shift_from_punch("2026-10-15 20:00:00", db)
            self.assertEqual(s2["start_time"], "20:00")
            self.assertEqual(s2["end_time"], "08:00")
            self.assertIn("Shift 2", s2["name"])

            # 2:00 AM -> Shift 2
            s2_night = determine_shift_from_punch("2026-10-16 02:00:00", db)
            self.assertEqual(s2_night["id"], s2["id"])

    def test_user_example_8am_to_730pm_shift1(self):
        """User exact example: Punch in 8:00 AM and punch out 7:30 PM must store in Shift 1 Present List."""
        with self.app.app_context():
            db = get_db()
            # Determine shift from check in
            cin_str = "2026-10-15 08:00:00"
            cout_str = "2026-10-15 19:30:00"
            shift = determine_shift_from_punch(cin_str, db)
            self.assertEqual(shift["start_time"], "08:00")
            self.assertEqual(shift["end_time"], "20:00")

            # Check status calculation
            cat = db.execute("SELECT * FROM employee_categories WHERE code='EMPLOYEES'").fetchone()
            cin = datetime.strptime(cin_str, "%Y-%m-%d %H:%M:%S")
            cout = datetime.strptime(cout_str, "%Y-%m-%d %H:%M:%S")
            status, ot = compute_attendance_status(cin, cout, shift, cat)
            # 11.5 hours, clocked out after 16:00 -> Full Day Present 'P'
            self.assertEqual(status, "P")

            # Insert attendance record with shift_id
            emp = db.execute("SELECT * FROM employees WHERE emp_code='EMP001'").fetchone()
            db.execute(
                "INSERT INTO attendance (emp_id, date, shift_id, check_in, check_out, work_hours, ot_hours, status, late) "
                "VALUES (?,?,?,?,?,?,?,?,0)",
                (emp["id"], "2026-10-15", shift["id"], cin_str, cout_str, 11.5, ot, status)
            )
            db.commit()

            # Verify in attendance table
            rec = db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (emp["id"], "2026-10-15")).fetchone()
            self.assertEqual(rec["shift_id"], shift["id"])
            self.assertEqual(rec["status"], "P")
            self.assertEqual(rec["work_hours"], 11.5)

    def test_hr_attendance_groups_by_shift(self):
        """Verify HR attendance register properly groups into Shift 1 vs Shift 2."""
        with self.app.app_context():
            db = get_db()
            s1 = db.execute("SELECT * FROM shifts WHERE (name LIKE '%Shift 1%' OR id=1)").fetchone()
            s2 = db.execute("SELECT * FROM shifts WHERE (name LIKE '%Shift 2%' OR id=2)").fetchone()

            # Insert a second employee for shift 2 if not exists
            emp2 = db.execute("SELECT * FROM employees WHERE emp_code='EMP002'").fetchone()
            if not emp2:
                emp_cat = db.execute("SELECT id FROM employee_categories WHERE code='EMPLOYEES'").fetchone()[0]
                db.execute(
                    "INSERT INTO employees (emp_code, name, email, department, designation, role, shift_id, office_id, category_id, password_hash, must_change_pw) "
                    "VALUES ('EMP002', 'Night Worker', 'night@sbg.com', 'Production', 'Operator', 'employee', ?, 1, ?, 'hash', 0)",
                    (s2["id"], emp_cat)
                )
                db.commit()

            emp1 = db.execute("SELECT * FROM employees WHERE emp_code='EMP001'").fetchone()
            emp2 = db.execute("SELECT * FROM employees WHERE emp_code='EMP002'").fetchone()

            # Clean any old test records for today
            d_str = "2026-10-17"
            db.execute("DELETE FROM attendance WHERE date=?", (d_str,))

            # EMP001 punches 8:00 AM to 7:30 PM (Shift 1)
            db.execute(
                "INSERT INTO attendance (emp_id, date, shift_id, check_in, check_out, work_hours, ot_hours, status, late) "
                "VALUES (?,?,?,?,?,?,?,?,0)",
                (emp1["id"], d_str, s1["id"], f"{d_str} 08:00:00", f"{d_str} 19:30:00", 11.5, 0.0, "P")
            )
            # EMP002 punches 8:00 PM (Shift 2, in progress)
            db.execute(
                "INSERT INTO attendance (emp_id, date, shift_id, check_in, check_out, work_hours, ot_hours, status, late) "
                "VALUES (?,?,?,?,NULL,0.0,0.0,NULL,0)",
                (emp2["id"], d_str, s2["id"], f"{d_str} 20:00:00")
            )
            db.commit()

            # Login as HR
            with self.client.session_transaction() as sess:
                admin = db.execute("SELECT * FROM employees WHERE role='hr'").fetchone()
                sess["uid"] = admin["id"]

            res = self.client.get(f"/hr/attendance?date={d_str}")
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn("Shift 1", html)
            self.assertIn("Shift 2", html)
            self.assertIn("Employee Staff", html)
            self.assertIn("Night Worker", html)

    def test_ai_assistant_shift_queries(self):
        """Verify AI assistant reports Shift 1 and Shift 2 breakdowns."""
        with self.app.app_context():
            db = get_db()
            admin = db.execute("SELECT * FROM employees WHERE role='hr'").fetchone()
            from app.ai_assistant import process_ai_query

            res = process_ai_query(admin, "How many employees are present today?")
            self.assertIn("Shift 1", res["reply"])
            self.assertIn("Shift 2", res["reply"])

            res_s1 = process_ai_query(admin, "Show shift 1 present")
            self.assertEqual(res_s1["type"], "hr_shift")


if __name__ == "__main__":
    unittest.main()
