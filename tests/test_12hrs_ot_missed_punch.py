import unittest
from datetime import datetime, date, timedelta
from app import create_app
from app.db import get_db
from app.services import compute_attendance_status, compute_status, finalize_attendance, day_code

class Test12HrsOTAndMissedPunch(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "REQUIRE_LIVENESS_MOTION": False, "MIN_PUNCH_GAP_MIN": 0})
        self.client = self.app.test_client()

    def test_compute_attendance_status_12hr_shift_no_false_ot(self):
        cat = {
            "name": "Employees",
            "code": "EMPLOYEES",
            "work_hours": 12.0,
            "overtime_hours": 0.0,
            "half_day_min_hours": 2.0,
            "early_full_day_cutoff": "16:00"
        }
        shift = {
            "id": 1,
            "name": "Morning Shift",
            "start_time": "08:00",
            "end_time": "20:00",
            "full_hours": 8.0,
            "ot_min_hours": 1.0,
            "grace_min": 10
        }
        # Exactly 12 hours
        cin = datetime(2026, 10, 30, 8, 0, 0)
        cout = datetime(2026, 10, 30, 20, 0, 0)
        status, ot = compute_attendance_status(cin, cout, shift, cat)
        self.assertEqual(status, "P")
        self.assertEqual(ot, 0.0)

        # 12 hours 15 mins (within 12.5h grace) - should still have 0.0 OT for Employees category
        cout_12h15m = datetime(2026, 10, 30, 20, 15, 0)
        status2, ot2 = compute_attendance_status(cin, cout_12h15m, shift, cat)
        self.assertEqual(status2, "P")
        self.assertEqual(ot2, 0.0)

    def test_compute_attendance_status_over_12_5_hours_is_missed_punch(self):
        cat = {
            "name": "Employees",
            "code": "EMPLOYEES",
            "work_hours": 12.0,
            "overtime_hours": 0.0,
            "half_day_min_hours": 2.0,
            "early_full_day_cutoff": "16:00"
        }
        shift = {"id": 1, "start_time": "08:00", "end_time": "20:00", "full_hours": 8.0, "ot_min_hours": 1.0, "grace_min": 10}
        # 16.51 hours (like Record 2: checked in 16:12 yesterday, out 08:43 today)
        cin = datetime(2026, 10, 28, 16, 12, 38)
        cout = datetime(2026, 10, 29, 8, 43, 14)
        status, ot = compute_attendance_status(cin, cout, shift, cat)
        self.assertEqual(status, "MP")
        self.assertEqual(ot, 0.0)

        upd = finalize_attendance({"check_in": "2026-10-28 16:12:38", "check_out": "2026-10-29 08:43:14"}, shift, cat)
        self.assertEqual(upd["status"], "MP")
        self.assertEqual(upd["work_hours"], 0.0)
        self.assertEqual(upd["ot_hours"], 0.0)

    def test_compute_status_over_12_5_hours_is_mp(self):
        shift = {"start_time": "08:00", "end_time": "20:00", "full_hours": 8.0, "ot_min_hours": 1.0}
        status, ot = compute_status(16.51, shift)
        self.assertEqual(status, "MP")
        self.assertEqual(ot, 0.0)

    def test_day_code_today_over_12_hours_returns_mp(self):
        with self.app.app_context():
            today_d = date(2026, 10, 30)
            # Check-in was 13 hours ago today
            cin_time = (datetime.now() - timedelta(hours=13)).strftime("%Y-%m-%d %H:%M:%S")
            rec = {"check_in": cin_time, "check_out": None, "status": None}
            code = day_code(today_d, rec, None, None, None, today_d, date(2026, 1, 1))
            self.assertEqual(code, "MP")

            # Check-in was 2 hours ago today -> should be "IN" (working)
            cin_time_recent = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            rec_recent = {"check_in": cin_time_recent, "check_out": None, "status": None}
            code_recent = day_code(today_d, rec_recent, None, None, None, today_d, date(2026, 1, 1))
            self.assertEqual(code_recent, "IN")

    def test_dashboard_removes_punchin_selfie_image(self):
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT * FROM employees WHERE emp_code='EMP001'").fetchone()

        res_login = self.client.post("/login/employee", data={"emp_code": "EMP001", "password": "123456789", "login_type": "employee"}, follow_redirects=False)
        self.assertEqual(res_login.status_code, 302)

        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Verify today-state section does NOT render any selfie <img> thumbnail
        today_state_idx = html.find('class="today-state"')
        self.assertNotEqual(today_state_idx, -1)
        today_state_html = html[today_state_idx:today_state_idx+1200]
        self.assertNotIn("<img", today_state_html)
        self.assertNotIn("View Punch In Photo", today_state_html)
        self.assertNotIn("View Punch Out Photo", today_state_html)
