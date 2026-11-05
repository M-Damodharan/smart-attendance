import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from werkzeug.security import generate_password_hash
from app import create_app
from app.db import get_db
from app.auth import _FAILED_ATTEMPT_KEYS
from app.services import coords_from_maps_link, coords_from_text, clear_shared_cache


class LocationSettingTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
        self.client = self.app.test_client()
        with self.app.app_context():
            _FAILED_ATTEMPT_KEYS.clear()
            clear_shared_cache()
            db = get_db()
            db.execute(
                "INSERT INTO employees (emp_code, name, email, department, role, password_hash, active, must_change_pw) "
                "VALUES ('HR001', 'HR Admin', 'hr@sbg.in', 'HR', 'hr', ?, 1, 0) "
                "ON CONFLICT(emp_code) DO UPDATE SET password_hash=?, active=1, must_change_pw=0",
                (generate_password_hash("admin123"), generate_password_hash("admin123"))
            )
            db.execute("DELETE FROM login_attempts")
            db.commit()

    def _login_hr(self):
        res = self.client.post(
            "/login/staff",
            data={"emp_code": "HR001", "password": "admin123", "login_type": "staff"},
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)

    def test_coordinate_parsing_helpers(self):
        # 1. Plain coordinates
        pt1 = coords_from_maps_link("12.743475, 77.817446")
        self.assertIsNotNone(pt1)
        self.assertAlmostEqual(pt1[0], 12.743475, places=4)
        self.assertAlmostEqual(pt1[1], 77.817446, places=4)

        # 2. DMS coordinates
        pt2 = coords_from_maps_link('12°44\'36.5"N 77°49\'02.8"E')
        self.assertIsNotNone(pt2)
        self.assertAlmostEqual(pt2[0], 12.743472, places=4)
        self.assertAlmostEqual(pt2[1], 77.817444, places=4)

        # 3. Google Maps link
        pt3 = coords_from_maps_link("https://www.google.com/maps/@12.743475,77.817446,17z")
        self.assertIsNotNone(pt3)
        self.assertAlmostEqual(pt3[0], 12.743475, places=4)
        self.assertAlmostEqual(pt3[1], 77.817446, places=4)

        # 4. Short maps link query param
        pt4 = coords_from_maps_link("https://maps.google.com/?q=12.743475,77.817446")
        self.assertIsNotNone(pt4)
        self.assertAlmostEqual(pt4[0], 12.743475, places=4)
        self.assertAlmostEqual(pt4[1], 77.817446, places=4)

    def test_hr_set_location_all_departments(self):
        self._login_hr()
        # Set all departments location via the quick GPS input bar
        res = self.client.post(
            "/hr/settings",
            data={
                "action": "office_link",
                "link": "12.750123, 77.820456",
                "id": "all",
                "radius_m": "5"
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("All department office locations successfully set", html)

        # Verify all active offices got updated in DB
        with self.app.app_context():
            db = get_db()
            offices = db.execute("SELECT lat, lng, radius_m FROM offices WHERE active=1").fetchall()
            self.assertTrue(len(offices) > 0)
            for o in offices:
                self.assertAlmostEqual(o["lat"], 12.750123, places=5)
                self.assertAlmostEqual(o["lng"], 77.820456, places=5)
                self.assertEqual(o["radius_m"], 5)

    def test_hr_set_location_single_department(self):
        self._login_hr()
        # Set specific department (e.g. office ID 1)
        res = self.client.post(
            "/hr/settings",
            data={
                "action": "office_link",
                "link": "12.760000, 77.830000",
                "id": "1",
                "radius_m": "5"
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("successfully set to 12.760000, 77.830000", html)

        with self.app.app_context():
            db = get_db()
            o1 = db.execute("SELECT lat, lng, radius_m FROM offices WHERE id=1").fetchone()
            self.assertAlmostEqual(o1["lat"], 12.760000, places=5)
            self.assertAlmostEqual(o1["lng"], 77.830000, places=5)
            self.assertEqual(o1["radius_m"], 5)

    def test_hr_save_location_card_with_pasted_combined_coords(self):
        self._login_hr()
        # Pasting "12.771234, 77.841234" directly into Latitude input box of department card
        res = self.client.post(
            "/hr/settings",
            data={
                "action": "office",
                "id": "1",
                "name": "Main Office",
                "department": "Staff",
                "lat": "12.771234, 77.841234",
                "lng": "",
                "radius_m": "5",
                "active": "1"
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("successfully saved to", html)

        with self.app.app_context():
            db = get_db()
            o = db.execute("SELECT lat, lng, radius_m FROM offices WHERE id=1").fetchone()
            self.assertAlmostEqual(o["lat"], 12.771234, places=5)
            self.assertAlmostEqual(o["lng"], 77.841234, places=5)

    def test_hr_set_location_with_place_name_no_500(self):
        """Setting location using a place name resolves or safely handles without 500 error."""
        self._login_hr()
        res = self.client.post(
            "/hr/settings",
            data={
                "action": "office_link",
                "link": "Hosur Bus Stand, Tamil Nadu",
                "id": "1",
                "radius_m": "5"
            },
            follow_redirects=True,
        )
        # Should cleanly either succeed or flash friendly error, NEVER crash with 500
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertTrue(
            "successfully set to" in html or "Could not read coordinates" in html or "Could not geocode" in html
        )

    def test_hr_set_location_ajax_returns_json_and_never_500s(self):
        """AJAX location setting returns JSON for smooth UI without page reload."""
        self._login_hr()
        res = self.client.post(
            "/hr/settings",
            data={
                "action": "office_link",
                "link": "12.743475, 77.817446",
                "id": "1",
                "radius_m": "5"
            },
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.is_json)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertAlmostEqual(data.get("lat"), 12.743475, places=5)
        self.assertAlmostEqual(data.get("lng"), 77.817446, places=5)

    def test_hr_save_office_card_with_invalid_coords_never_500s(self):
        """Saving an office card with invalid non-numeric coords flashes error and never 500s."""
        self._login_hr()
        res = self.client.post(
            "/hr/settings",
            data={
                "action": "office",
                "id": "1",
                "name": "Main Office",
                "department": "Staff",
                "lat": "not_a_number",
                "lng": "also_invalid",
                "radius_m": "5",
                "active": "1"
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Could not parse coordinates", html)


if __name__ == "__main__":
    unittest.main()
