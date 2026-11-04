import os
import sys
import unittest
import json
import base64
from io import BytesIO
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import create_app
from app.db import get_db
from app.services import haversine

class Geofence5mAndAccuracyTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
        self.client = self.app.test_client()

        with self.app.app_context():
            db = get_db()
            # Ensure Main Office exists with 5m radius
            off = db.execute("SELECT id, lat, lng, radius_m FROM offices WHERE id=1").fetchone()
            if not off:
                db.execute("INSERT OR REPLACE INTO offices (id, name, department, lat, lng, radius_m, active) VALUES (1, 'Main Office', 'General', 12.762733, 77.802695, 5, 1)")
            else:
                db.execute("UPDATE offices SET radius_m=5, lat=12.762733, lng=77.802695 WHERE id=1")
            
            # Ensure system settings max_gps_accuracy_m is 100
            db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '100')")
            db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")

            # Employee setup
            emp = db.execute("SELECT id, emp_code FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
            if not emp:
                from werkzeug.security import generate_password_hash
                emp_id = db.execute("INSERT INTO employees (emp_code, name, role, office_id, password_hash, must_change_pw, active, face_registered) VALUES ('EMP_GEO_TEST', 'Geo Test User', 'employee', 1, ?, 0, 1, 1)", (generate_password_hash("pass123"),)).lastrowid
                self.emp_id = emp_id
                self.emp_code = "EMP_GEO_TEST"
            else:
                self.emp_id = emp["id"]
                self.emp_code = emp["emp_code"]
                db.execute("UPDATE employees SET office_id=1, category_id=1, department='Staff', must_change_pw=0, active=1 WHERE id=?", (self.emp_id,))
            
            # Clear today's attendance and child records for clean test
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("DELETE FROM late_coming_requests WHERE emp_id=?", (self.emp_id,))
            db.execute("DELETE FROM comments WHERE entity_type='attendance' AND entity_id IN (SELECT id FROM attendance WHERE emp_id=?)", (self.emp_id,))
            db.execute("DELETE FROM attendance WHERE emp_id=? AND date=date('now')", (self.emp_id,))
            db.execute("PRAGMA foreign_keys = ON")
            db.commit()

        self._login()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["uid"] = self.emp_id
            sess["role"] = "employee"
            sess["name"] = "Geo Test User"

    def _make_dummy_selfie(self):
        # Create a bright 200x200 image with high contrast face-like content to pass clarity checks
        im = Image.new("RGB", (200, 200), (180, 180, 180))
        # Draw some features
        for x in range(70, 130):
            for y in range(70, 130):
                im.putpixel((x, y), (80, 80, 80))
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=90)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    def test_01_main_office_radius_is_5_meters(self):
        """Verify Main Office has radius_m=5 in database."""
        with self.app.app_context():
            db = get_db()
            off = db.execute("SELECT name, radius_m FROM offices WHERE id=1").fetchone()
            self.assertEqual(off["radius_m"], 5)
            self.assertIn("Main", off["name"])

    def test_02_inside_5m_with_smartphone_accuracy_accepted(self):
        """When distance <= 5m and smartphone accuracy is typical (e.g. 25m), attendance is accepted."""
        # Main Office coords: 12.762733, 77.802695
        # Coordinates ~2.5m away:
        lat = 12.762733 + 0.000020
        lng = 77.802695
        dist = haversine(lat, lng, 12.762733, 77.802695)
        self.assertLess(dist, 5.0)

        # Normal phone indoors gets ±25m accuracy: MUST BE ACCEPTED
        res = self.client.post("/api/geo-check", json={"lat": lat, "lng": lng, "accuracy": 25.0})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["inside"])
        self.assertTrue(data["allowed"])
        self.assertTrue(data["gps_accurate"])
        self.assertEqual(data["radius"], 5.0)
        self.assertEqual(data["rules"], "5 meters inside accepted")
        self.assertAlmostEqual(data["distance"], dist, delta=0.5)

    def test_03_outside_5m_blocked(self):
        """When distance > 5m, attendance is strictly blocked."""
        # Coordinates ~12m away: 0.00010 deg lat ~ 11.1m
        lat = 12.762733 + 0.000110
        lng = 77.802695
        dist = haversine(lat, lng, 12.762733, 77.802695)
        self.assertGreater(dist, 5.0)

        res = self.client.post("/api/geo-check", json={"lat": lat, "lng": lng, "accuracy": 15.0})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["inside"])
        self.assertFalse(data["allowed"])
        self.assertGreater(data["distance"], 5.0)

    def test_04_inaccurate_gps_reading_above_limit_blocked(self):
        """Attendance cannot be approved if GPS accuracy exceeds maximum threshold (>100m)."""
        lat = 12.762733
        lng = 77.802695

        res = self.client.post("/api/geo-check", json={"lat": lat, "lng": lng, "accuracy": 250.0})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertFalse(data.get("gps_accurate", False))
        self.assertIn("GPS accuracy too low", data["error"])

    def test_05_invalid_or_negative_gps_accuracy_rejected(self):
        """Missing or zero/negative GPS accuracy is rejected."""
        res = self.client.post("/api/geo-check", json={"lat": 12.762733, "lng": 77.802695, "accuracy": 0})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("invalid", data["error"].lower())

    def test_06_punch_endpoint_blocks_outside_5m(self):
        """The /api/punch endpoint strictly blocks check-in outside 5m radius."""
        lat = 12.762733 + 0.000200  # ~22 meters away
        lng = 77.802695
        selfie = self._make_dummy_selfie()

        payload = {
            "kind": "in",
            "lat": lat,
            "lng": lng,
            "accuracy": 18.0,
            "selfie": selfie,
            "webauthn_assertion": {
                "id": "test_cred_id",
                "rawId": "test_cred_id",
                "response": {
                    "clientDataJSON": base64.b64encode(b'{"type":"webauthn.get"}').decode("ascii"),
                    "authenticatorData": base64.b64encode(b'auth_data_mock').decode("ascii"),
                    "signature": base64.b64encode(b'mock_sig').decode("ascii")
                }
            }
        }
        res = self.client.post("/api/punch", json=payload)
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("Attendance blocked", data["error"])
        self.assertIn("5 meters inside accepted", data["error"])

    def test_07_punch_endpoint_blocks_wildly_inaccurate_gps(self):
        """The /api/punch endpoint blocks check-in if GPS reading is beyond tolerance (>100m)."""
        lat = 12.762733
        lng = 77.802695
        selfie = self._make_dummy_selfie()

        payload = {
            "kind": "in",
            "lat": lat,
            "lng": lng,
            "accuracy": 350.0,  # Highly inaccurate cell tower fix
            "selfie": selfie,
            "webauthn_assertion": {"id": "mock"}
        }
        res = self.client.post("/api/punch", json=payload)
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("GPS accuracy too low", data["error"])

    def test_08_exact_whatsapp_screenshot_scenario_accepted(self):
        """User screenshot test: 0.1m distance from office with ±23.1m smartphone accuracy MUST be accepted."""
        lat = 12.762733
        lng = 77.802695
        # Distance is 0.0m (or ~0.1m) and accuracy is 23.1m
        res = self.client.post("/api/geo-check", json={"lat": lat, "lng": lng, "accuracy": 23.1})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["inside"])
        self.assertTrue(data["allowed"])
        self.assertTrue(data["gps_accurate"])
        self.assertLessEqual(data["distance"], 5.0)
        self.assertEqual(data["rules"], "5 meters inside accepted")

if __name__ == "__main__":
    unittest.main()
