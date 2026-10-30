import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from app import create_app
from app.db import get_db
from app.services import coords_from_maps_link, coords_from_text


class LocationSettingTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
        self.client = self.app.test_client()

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

    def test_hr_set_location_all_departments(self):
        self._login_hr()
        res = self.client.post(
            "/hr/settings",
            data={
                "action": "office_link",
                "link": "12.743475, 77.817446",
                "id": "all",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("All department office locations successfully set", html)

        with self.app.app_context():
            db = get_db()
            rows = db.execute("SELECT lat, lng, radius_m FROM offices WHERE active=1").fetchall()
            for r in rows:
                self.assertAlmostEqual(r["lat"], 12.743475, places=4)
                self.assertAlmostEqual(r["lng"], 77.817446, places=4)
                self.assertEqual(r["radius_m"], 30)

    def test_hr_set_location_individual_office(self):
        self._login_hr()
        res = self.client.post(
            "/hr/settings",
            data={
                "action": "office_link",
                "link": "12.762733, 77.802695",
                "id": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("successfully set to", html)

        with self.app.app_context():
            db = get_db()
            r = db.execute("SELECT lat, lng, radius_m FROM offices WHERE id=1").fetchone()
            self.assertAlmostEqual(r["lat"], 12.762733, places=4)
            self.assertAlmostEqual(r["lng"], 77.802695, places=4)
            self.assertEqual(r["radius_m"], 30)

    def test_hr_save_location_card_with_pasted_combined_coords(self):
        self._login_hr()
        # Admin pastes "12.750000, 77.810000" into Latitude input
        res = self.client.post(
            "/hr/settings",
            data={
                "action": "office",
                "id": "2",
                "name": "General Office",
                "department": "",
                "lat": "12.750000, 77.810000",
                "lng": "",
                "radius_m": "30",
                "active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("successfully saved to", html)

        with self.app.app_context():
            db = get_db()
            r = db.execute("SELECT lat, lng, radius_m FROM offices WHERE id=2").fetchone()
            self.assertAlmostEqual(r["lat"], 12.750000, places=4)
            self.assertAlmostEqual(r["lng"], 77.810000, places=4)


if __name__ == "__main__":
    unittest.main()
