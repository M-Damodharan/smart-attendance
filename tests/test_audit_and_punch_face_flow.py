import io
import os
import unittest
from PIL import Image
from app import create_app
from app.db import get_db

class TestAuditAndPunchFaceFlow(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "REQUIRE_LIVENESS_MOTION": False, "MIN_PUNCH_GAP_MIN": 0})
        self.client = self.app.test_client()

    def test_all_pages_respond_200(self):
        # 1. Public pages
        for path in ["/", "/login", "/login/staff", "/login/employee", "/login/contract", "/login/accounts", "/healthz", "/sw.js"]:
            res = self.client.get(path)
            self.assertIn(res.status_code, (200, 302), f"Public route failed: {path}")

        # 2. Employee login & pages
        res_login = self.client.post("/login/employee", data={"emp_code": "EMP001", "password": "123456789", "login_type": "employee"}, follow_redirects=False)
        self.assertEqual(res_login.status_code, 302)

        emp_paths = [
            "/dashboard", "/punch", "/history", "/history/export", "/leave", "/od",
            "/permission", "/face-change", "/change-password", "/api/notifications",
            "/api/webauthn/status", "/api/android-biometric/challenge"
        ]
        for path in emp_paths:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, f"Employee route failed: {path}")

        # 3. HR login & pages
        res_hr = self.client.post("/login/staff", data={"emp_code": "HR001", "password": "admin123", "login_type": "staff"}, follow_redirects=False)
        self.assertEqual(res_hr.status_code, 302)

        hr_paths = [
            "/hr/dashboard", "/hr/employees", "/hr/employees/new", "/hr/shifts",
            "/hr/approvals", "/hr/attendance", "/hr/corrections", "/hr/reports",
            "/hr/audit", "/hr/settings", "/hr/face-security-audit",
            "/hr/cloud-backup-sync", "/hr/database/download-db"
        ]
        for path in hr_paths:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, f"HR route failed: {path}")

    def test_punch_in_out_and_face_visibility_on_dashboard(self):
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT id, office_id FROM employees WHERE emp_code='EMP001'").fetchone()
            emp_id = emp["id"]
            from app.services import today, d2s
            db.execute("DELETE FROM attendance WHERE emp_id=? AND date=?", (emp_id, d2s(today())))
            db.execute(
                "INSERT OR REPLACE INTO webauthn_credentials (emp_id, credential_id, public_key, sign_count, device_name, aaguid) "
                "VALUES (?, 'test_hw_device_audit', 'sig_test', 0, 'Android Native Face ID', 'android-biometric')",
                (emp_id,)
            )
            db.execute("UPDATE employees SET device_registered=1, face_registered=1 WHERE id=?", (emp_id,))
            db.commit()

            office = db.execute("SELECT * FROM offices WHERE id=?", (emp["office_id"] or 1,)).fetchone() or db.execute("SELECT * FROM offices WHERE active=1 LIMIT 1").fetchone()
            lat, lng = office["lat"], office["lng"]

        # Login
        self.client.post("/login/employee", data={"emp_code": "EMP001", "password": "123456789", "login_type": "employee"})

        # Get Challenge
        ch_res = self.client.get("/api/android-biometric/challenge")
        ch = ch_res.get_json()["challenge"]

        # Read sample face image
        import base64
        sample_path = os.path.join("backups", "latest", "selfies", "EMP001_20261015_in_084649.jpg")
        with open(sample_path, "rb") as f:
            selfie_data = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")

        # Punch In
        punch_in_res = self.client.post("/api/punch", json={
            "kind": "in",
            "lat": lat,
            "lng": lng,
            "accuracy": 10.0,
            "selfie": selfie_data,
            "android_biometric_assertion": {
                "id": "test_hw_device_audit",
                "type": "android_biometric",
                "signature": "sig_test",
                "challenge": ch
            }
        })
        self.assertEqual(punch_in_res.status_code, 200)
        p_data = punch_in_res.get_json()
        self.assertTrue(p_data["ok"])
        photo_url = p_data.get("photo_url")
        self.assertIsNotNone(photo_url)

        # Verify photo_url is accessible
        photo_res = self.client.get(photo_url)
        self.assertEqual(photo_res.status_code, 200)
        self.assertIn("image/", photo_res.content_type)

        # Verify Dashboard renders check-in and photo
        dash_res = self.client.get("/dashboard")
        dash_html = dash_res.get_data(as_text=True)
        self.assertIn("Checked in at", dash_html)
        self.assertIn("Verified Face Attendance", dash_html)

        # Verify History renders thumbnail
        hist_res = self.client.get("/history")
        hist_html = hist_res.get_data(as_text=True)
        self.assertIn("View Punch Face Selfie", hist_html)
