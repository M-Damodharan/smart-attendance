import unittest
from datetime import datetime, date, timedelta
from app import create_app
from app.db import get_db
from app.services import d2s, today, check_and_sync_missed_punches

class TestNextDayMPAttendance(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "REQUIRE_LIVENESS_MOTION": False, "MIN_PUNCH_GAP_MIN": 0})
        self.client = self.app.test_client()

    def test_day1_in_no_out_auto_mp_and_day2_in_independent(self):
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT * FROM employees WHERE emp_code='EMP001'").fetchone()
            self.assertIsNotNone(emp)

            # Ensure clean state for test dates
            t = today()
            d_yesterday = d2s(t - timedelta(days=1))
            d_today = d2s(t)
            db.execute("DELETE FROM attendance WHERE emp_id=? AND date IN (?, ?)", (emp["id"], d_yesterday, d_today))
            db.execute("DELETE FROM notifications WHERE message LIKE ?", (f"%{emp['emp_code']}%",))
            db.commit()

            # 1. Day 1: Employee punches IN at 8:00 AM (e.g. yesterday)
            cin_yesterday = f"{d_yesterday} 08:00:00"
            db.execute("""
                INSERT INTO attendance (emp_id, date, shift_id, check_in, in_lat, in_lng, in_dist, in_geo_ok, status)
                VALUES (?, ?, ?, ?, 11.0, 77.0, 5, 1, 'P')
            """, (emp["id"], d_yesterday, emp["shift_id"] or 1, cin_yesterday))
            db.commit()

            # Run 12-hour automated missed punch checker
            check_and_sync_missed_punches(db)

            # Verify Day 1 record is marked as MP with exact remark and NO fake OUT time
            rec1 = db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (emp["id"], d_yesterday)).fetchone()
            self.assertEqual(rec1["status"], "MP")
            self.assertIsNone(rec1["check_out"], "False checkout records must not be generated")
            self.assertEqual(rec1["work_hours"], 0.0)
            self.assertEqual(rec1["ot_hours"], 0.0)
            self.assertEqual(rec1["remarks"], "Auto Marked – OUT Punch Not Recorded Within 12 Hours.")

            # Verify HR notification was generated
            notif = db.execute(
                "SELECT * FROM notifications WHERE target_role='hr' AND message LIKE ? ORDER BY id DESC LIMIT 1",
                (f"%{rec1['id']}%",)
            ).fetchone()
            self.assertIsNotNone(notif)
            self.assertIn("Auto Marked – OUT Punch Not Recorded Within 12 Hours.", notif["message"])

        # 2. Day 2: Employee logs in and views dashboard
        res_login = self.client.post("/login/employee", data={"emp_code": "EMP001", "password": "123456789", "login_type": "employee"}, follow_redirects=False)
        self.assertEqual(res_login.status_code, 302)

        resp_dash = self.client.get("/dashboard")
        self.assertEqual(resp_dash.status_code, 200)
        dash_html = resp_dash.get_data(as_text=True)

        # Dashboard shows previous day's MP record separately
        self.assertIn("Previous Day Missed Punch (MP)", dash_html)
        self.assertIn("Auto Marked – OUT Punch Not Recorded Within 12 Hours.", dash_html)

        # Dashboard allows employee to check in for today (Not checked in today + Check in button)
        self.assertIn("Not checked in today", dash_html)
        self.assertIn("Check in", dash_html)

        # 3. Employee visits /punch
        resp_punch = self.client.get("/punch")
        self.assertEqual(resp_punch.status_code, 200)
        punch_html = resp_punch.get_data(as_text=True)

        # Punch page mode must be "in", NOT stuck in "out"
        self.assertIn('data-mode="in"', punch_html)
        self.assertIn("Check in", punch_html)

        # 4. Day 2: Employee punches IN today
        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT OR REPLACE INTO webauthn_credentials (emp_id, credential_id, public_key, sign_count, device_name, aaguid) "
                "VALUES (?, 'test_hw_mp', 'sig_test', 0, 'Android Native Face ID', 'android-biometric')",
                (emp["id"],)
            )
            db.execute("UPDATE employees SET device_registered=1, face_registered=1 WHERE id=?", (emp["id"],))
            db.commit()

        ch_res = self.client.get("/api/android-biometric/challenge")
        ch = ch_res.get_json()["challenge"]

        # Clean selfie fixture
        import io, base64, os
        sample_path = os.path.join("backups", "latest", "selfies", "EMP001_20261015_in_084649.jpg")
        if os.path.exists(sample_path):
            with open(sample_path, "rb") as f:
                selfie_b64 = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
        else:
            img = Image.new("RGB", (16, 16), color=(200, 200, 200))
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            selfie_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

        # Mock geofence inside department office
        with self.app.app_context():
            db = get_db()
            office = db.execute("SELECT * FROM offices WHERE id=?", (emp["office_id"] or 1,)).fetchone() or db.execute("SELECT * FROM offices WHERE active=1 LIMIT 1").fetchone()
            lat, lng = office["lat"], office["lng"]

        resp_api = self.client.post("/api/punch", json={
            "kind": "in",
            "lat": lat,
            "lng": lng,
            "accuracy": 10.0,
            "selfie": selfie_b64,
            "android_biometric_assertion": {
                "id": "test_hw_mp",
                "type": "android_biometric",
                "signature": "sig_test",
                "challenge": ch
            }
        })
        self.assertEqual(resp_api.status_code, 200, resp_api.get_data(as_text=True))
        data = resp_api.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("punch_kind"), "in")

        # 5. Verify records in database
        with self.app.app_context():
            db = get_db()
            # Day 1 record remains MP with 0 hours and NO fake OUT time
            d1_rec = db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (emp["id"], d_yesterday)).fetchone()
            self.assertEqual(d1_rec["status"], "MP")
            self.assertIsNone(d1_rec["check_out"])

            # Day 2 record is active with check_in recorded today
            d2_rec = db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (emp["id"], d_today)).fetchone()
            self.assertIsNotNone(d2_rec)
            self.assertIsNotNone(d2_rec["check_in"])
            self.assertIsNone(d2_rec["check_out"])

            # Clean up test records
            db.execute("DELETE FROM attendance WHERE emp_id=? AND date IN (?, ?)", (emp["id"], d_yesterday, d_today))
            db.commit()

    def test_hr_missed_punch_list_and_resolution(self):
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT * FROM employees WHERE emp_code='EMP001'").fetchone()
            hr = db.execute("SELECT * FROM employees WHERE role='hr'").fetchone()
            self.assertIsNotNone(emp)
            self.assertIsNotNone(hr)

            t = today()
            test_date = d2s(t - timedelta(days=2))
            db.execute("DELETE FROM attendance WHERE emp_id=? AND date=?", (emp["id"], test_date))
            db.commit()

            # Insert unclosed punch 2 days ago
            db.execute("""
                INSERT INTO attendance (emp_id, date, shift_id, check_in, status, remarks, mp_resolved)
                VALUES (?, ?, ?, ?, 'MP', 'Auto Marked – OUT Punch Not Recorded Within 12 Hours.', 0)
            """, (emp["id"], test_date, emp["shift_id"] or 1, f"{test_date} 08:30:00"))
            db.commit()
            att_id = db.execute("SELECT id FROM attendance WHERE emp_id=? AND date=?", (emp["id"], test_date)).fetchone()["id"]

        # Log in as HR
        res_hr_login = self.client.post("/login/staff", data={"emp_code": "HR001", "password": "admin123", "login_type": "staff"}, follow_redirects=False)
        self.assertEqual(res_hr_login.status_code, 302)

        # Check /hr/missed-punches page
        resp_hr = self.client.get("/hr/missed-punches")
        self.assertEqual(resp_hr.status_code, 200)
        hr_html = resp_hr.get_data(as_text=True)
        self.assertIn("HR – Missed Punch List", hr_html)
        self.assertIn("EMP001", hr_html)
        self.assertIn("Auto Marked – OUT Punch Not Recorded Within 12 Hours.", hr_html)

        # HR Resolves with actual OUT punch
        resp_resolve = self.client.post("/hr/missed-punches/resolve", data={
            "att_id": att_id,
            "action": "correct_out",
            "check_out": "20:30",
            "reason": "Employee forgot to punch OUT before leaving work per floor supervisor log"
        }, follow_redirects=True)
        self.assertEqual(resp_resolve.status_code, 200)

        # Verify resolution saved in DB
        with self.app.app_context():
            db = get_db()
            resolved = db.execute("SELECT * FROM attendance WHERE id=?", (att_id,)).fetchone()
            self.assertEqual(resolved["mp_resolved"], 1)
            self.assertIsNotNone(resolved["check_out"])
            self.assertEqual(resolved["remarks"], "Employee forgot to punch OUT before leaving work per floor supervisor log")
            self.assertEqual(resolved["mp_resolved_by"], hr["name"])

            # Clean up
            db.execute("DELETE FROM attendance WHERE id=?", (att_id,))
            db.commit()

if __name__ == "__main__":
    unittest.main()
