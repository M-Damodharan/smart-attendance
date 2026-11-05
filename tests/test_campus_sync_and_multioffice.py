import os
import sys
import unittest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import create_app
from app.db import get_db
from app.api import evaluate_geo, get_department_office
from app.services import haversine, clear_shared_cache

class CampusSyncAndMultiOfficeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
        self.client = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            clear_shared_cache()
            # Ensure offices 1 and 5 exist
            # Office 1: Main Office at 12.762711, 77.802680 (25.8m away from 12.762943)
            # Office 5: Accounts & Finance at 12.762943, 77.802698
            db.execute("INSERT OR REPLACE INTO offices (id, name, department, lat, lng, radius_m, active) VALUES (1, 'Main Office', 'Staff', 12.762711, 77.802680, 5, 1)")
            db.execute("INSERT OR REPLACE INTO offices (id, name, department, lat, lng, radius_m, active) VALUES (5, 'Accounts & Finance', 'Accounts & Finance', 12.762943, 77.802698, 5, 1)")
            
            # Reset geofence tolerance to 0 for strict test
            db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")
            db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '100')")
            
            # Ensure test employee SBG001 has office_id = 5
            emp = db.execute("SELECT id FROM employees WHERE emp_code='SBG001'").fetchone()
            pw = generate_password_hash("pass123")
            if emp:
                db.execute("UPDATE employees SET office_id=5, department='Staff', active=1, must_change_pw=0 WHERE emp_code='SBG001'")
            else:
                db.execute("INSERT INTO employees (emp_code, name, email, department, role, office_id, password_hash, active, must_change_pw) VALUES ('SBG001', 'Damodharan M', 'damo@sbg.in', 'Staff', 'employee', 5, ?, 1, 0)", (pw,))
            db.commit()
            clear_shared_cache()

    def test_employee_assigned_office_priority(self):
        with self.app.app_context():
            db = get_db()
            user = db.execute("SELECT * FROM employees WHERE emp_code='SBG001'").fetchone()
            office = get_department_office(db, user)
            # Employee explicitly assigned to office_id = 5 (Accounts & Finance)
            self.assertEqual(office["id"], 5)
            self.assertEqual(office["name"], "Accounts & Finance")

    def test_evaluate_geo_inside_assigned_office(self):
        with self.app.app_context():
            db = get_db()
            user = db.execute("SELECT * FROM employees WHERE emp_code='SBG001'").fetchone()
            # User's GPS coordinates exactly at Office 5: 12.762943, 77.802698
            res = evaluate_geo(user, 12.762943, 77.802698, 7.0)
            self.assertTrue(res["ok"])
            self.assertTrue(res["inside"])
            self.assertLessEqual(res["distance"], 1.0)
            self.assertEqual(res["office"], "Accounts & Finance")

    def test_evaluate_geo_campus_fallback(self):
        with self.app.app_context():
            db = get_db()
            # Employee without assigned office, dept='Staff' which matches Main Office (at 12.762711, 77.802680)
            user = dict(id=9999, emp_code='SBG_UNASSIGNED', name='Campus Walker', department='Staff', office_id=None, role='employee')
            # But employee is standing at Office 5 (12.762943, 77.802698)
            # Without multi-office fallback, dist to Main Office is 25.8m and would be blocked.
            # With multi-office fallback, it recognizes presence inside an active company office!
            res = evaluate_geo(user, 12.762943, 77.802698, 7.0)
            self.assertTrue(res["ok"])
            self.assertTrue(res["inside"])
            self.assertLessEqual(res["distance"], 1.0)
            self.assertTrue(len(res["office"]) > 0)

    def test_apply_to_all_campus_sync_hr_action(self):
        with self.app.app_context():
            # Login as HR admin
            db = get_db()
            hr_user = db.execute("SELECT id FROM employees WHERE role='hr' AND active=1 LIMIT 1").fetchone()
            pw = generate_password_hash("pass123")
            if not hr_user:
                hr_id = db.execute("INSERT INTO employees (emp_code, name, email, department, role, password_hash, active, must_change_pw) VALUES ('HR_TEST_SYNC', 'HR Admin', 'hr_sync@sbg.in', 'HR', 'hr', ?, 1, 0)", (pw,)).lastrowid
            else:
                hr_id = hr_user["id"]
                db.execute("UPDATE employees SET must_change_pw=0, active=1 WHERE id=?", (hr_id,))
            db.commit()

        with self.client.session_transaction() as sess:
            sess["uid"] = hr_id
            sess["role"] = "hr"
            sess["_fresh"] = True

        # Post office update with apply_to_all=1
        new_lat = 12.762943
        new_lng = 77.802698
        res = self.client.post("/hr/settings", data={
            "action": "office",
            "id": 1,
            "name": "Main Office",
            "department": "Staff",
            "lat": str(new_lat),
            "lng": str(new_lng),
            "radius_m": "5",
            "active": "1",
            "apply_to_all": "1",
        }, headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["apply_to_all"])

        # Verify all active offices now have the updated lat, lng, radius_m
        with self.app.app_context():
            db = get_db()
            all_offices = db.execute("SELECT * FROM offices WHERE active=1").fetchall()
            for off in all_offices:
                self.assertAlmostEqual(off["lat"], new_lat, places=5)
                self.assertAlmostEqual(off["lng"], new_lng, places=5)
                self.assertEqual(off["radius_m"], 5)

if __name__ == "__main__":
    unittest.main()
