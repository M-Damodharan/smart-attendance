import base64
import io
import json
import os
import re
import shutil
import tempfile
import unittest

import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import numpy as np
from PIL import Image, ImageDraw

from app import create_app
from app.db import get_db, init_db
from app.face_engine import (
    register_face_for_employee,
    verify_face_for_employee,
)


def make_test_face(person_id: int, brightness: float = 1.0) -> str:
    """Generates synthetic face image for tests with distinct geometry."""
    im = Image.new("RGB", (128, 128), (20, 20, 20))
    d = ImageDraw.Draw(im)
    if person_id == 1:
        # Person 1 (Damodharan)
        d.ellipse([20, 20, 100, 100], fill=(230, 200, 170))
        d.ellipse([40, 50, 50, 60], fill=(20, 20, 20))
        d.ellipse([70, 50, 80, 60], fill=(20, 20, 20))
        d.rectangle([45, 80, 75, 86], fill=(160, 50, 50))
    elif person_id == 2:
        # Person 2 (Staff)
        d.ellipse([30, 15, 95, 110], fill=(210, 180, 150))
        d.ellipse([45, 45, 55, 55], fill=(30, 30, 30))
        d.ellipse([65, 45, 75, 55], fill=(30, 30, 30))
        d.rectangle([48, 85, 72, 90], fill=(180, 60, 60))
    else:
        # Person 3 (Stranger / Different)
        d.rectangle([25, 25, 100, 100], fill=(240, 240, 240))
        d.rectangle([35, 40, 50, 55], fill=(10, 10, 10))
        d.rectangle([70, 40, 85, 55], fill=(10, 10, 10))
        d.ellipse([45, 75, 75, 95], fill=(50, 50, 50))

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class TestAndroidBiometricAndTwoImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_android_bio.db")
        cls.selfie_dir = os.path.join(cls.tmpdir, "selfies")
        os.makedirs(cls.selfie_dir, exist_ok=True)

        cls.app = create_app({
            "TESTING": True,
            "DATABASE": cls.db_path,
            "SELFIE_DIR": cls.selfie_dir,
            "WTF_CSRF_ENABLED": False,
            "SERVER_NAME": "localhost",
            "DEMO_DATA": True,
        })
        with cls.app.app_context():
            init_db(cls.app)
            db = get_db()
            db.execute("UPDATE offices SET lat=12.9716, lng=77.5946, radius_m=200 WHERE id=1")
            db.execute("UPDATE employees SET office_id=1, must_change_pw=0")
            db.commit()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _get_csrf(self, client, url="/login"):
        res = client.get(url)
        txt = res.get_data(as_text=True)
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', txt)
        if not m:
            m = re.search(r'name="csrf-token"\s+content="([^"]+)"', txt)
        return m.group(1) if m else ""

    def _login(self, client, emp_code, password):
        client.get("/logout")
        token = self._get_csrf(client, "/login")
        return client.post("/login", data={"emp_code": emp_code, "password": password, "csrf_token": token}, follow_redirects=True)

    def test_01_two_image_registration_success(self):
        """1. Two-Image Registration stores dual-reference photos and allows punch."""
        with self.app.app_context():
            db = get_db()
            e = db.execute("SELECT id FROM employees WHERE emp_code='EMP001'").fetchone()
            p1 = make_test_face(2, brightness=1.0)
            p2 = make_test_face(2, brightness=0.95)

            ok, msg, _ = register_face_for_employee(db, e["id"], {"photo1": p1, "photo2": p2}, registered_by_id=1, force=True)
            self.assertTrue(ok)
            self.assertIn("successfully", msg.lower())

            # Check template contents
            user = db.execute("SELECT face_registered, face_template, face_image FROM employees WHERE id=?", (e["id"],)).fetchone()
            self.assertEqual(user["face_registered"], 1)
            self.assertIsNotNone(user["face_image"])

            tpl = json.loads(user["face_template"])
            self.assertIn("photo1", tpl["angles"])
            self.assertIn("photo2", tpl["angles"])
            self.assertIn("photo1_file", tpl)
            self.assertIn("photo2_file", tpl)

            # Verification of punch against Photo 1
            res1 = verify_face_for_employee(db, e["id"], p1)
            self.assertTrue(res1["verified"])

            # Verification of punch against Photo 2
            res2 = verify_face_for_employee(db, e["id"], p2)
            self.assertTrue(res2["verified"])

    def test_02_two_image_different_people_rejected(self):
        """2. Uploading two completely different people as Photo 1 and Photo 2 is rejected."""
        with self.app.app_context():
            db = get_db()
            e = db.execute("SELECT id FROM employees WHERE emp_code='EMP001'").fetchone()
            p1 = make_test_face(1)  # Person 1
            p2 = make_test_face(3)  # Person 3 (Different person)

            ok, msg, _ = register_face_for_employee(db, e["id"], {"photo1": p1, "photo2": p2}, registered_by_id=1, force=True)
            self.assertFalse(ok)
            self.assertIn("do not match", msg.lower())

    def test_03_android_biometric_challenge_and_device_registration(self):
        """3. Android Native Biometric challenge and device binding."""
        c = self.app.test_client()
        self._login(c, "E0012", "123456789")

        # Request challenge
        ch_res = c.get("/api/android-biometric/challenge")
        ch_data = ch_res.get_json()
        self.assertIsNotNone(ch_data)
        self.assertTrue(ch_data["ok"])
        self.assertIn("challenge", ch_data)
        self.assertEqual(ch_data["emp_code"], "E0012")

        # Register device hardware ID
        reg_res = c.post("/api/android-biometric/register-device", json={
            "device_id": "android_hw_device_001_test",
            "signature": "sig_test_hardware_signature",
            "challenge": ch_data["challenge"]
        })
        reg_data = reg_res.get_json()
        self.assertTrue(reg_data["ok"])
        self.assertIn("successfully bound", reg_data["message"].lower())

        with self.app.app_context():
            db = get_db()
            user = db.execute("SELECT device_registered, face_registered FROM employees WHERE emp_code='E0012'").fetchone()
            self.assertEqual(user["device_registered"], 1)
            self.assertEqual(user["face_registered"], 1)

    def test_04_android_biometric_device_sharing_blocked(self):
        """4. One Phone, One Employee: Another employee cannot register the same phone."""
        c = self.app.test_client()
        self._login(c, "EMP001", "123456789")

        ch_res = c.get("/api/android-biometric/challenge")
        ch_data = ch_res.get_json()
        self.assertIsNotNone(ch_data)
        self.assertTrue(ch_data["ok"])

        # Attempt to register the exact same phone hardware device
        reg_res = c.post("/api/android-biometric/register-device", json={
            "device_id": "android_hw_device_001_test",
            "signature": "sig_test_hardware_signature",
            "challenge": ch_data["challenge"]
        })
        reg_data = reg_res.get_json()
        self.assertEqual(reg_res.status_code, 403)
        self.assertFalse(reg_data["ok"])
        self.assertIn("already registered to employee", reg_data["error"].lower())

    def test_05_android_biometric_punch_in_and_out_success(self):
        """5. Native Android Biometric Punch In and Out logs attendance reliably."""
        c = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            e_damo = db.execute("SELECT id FROM employees WHERE emp_code='E0012'").fetchone()
            emp_id = e_damo["id"]
            exist = db.execute("SELECT id FROM webauthn_credentials WHERE credential_id='android_hw_device_001_test'").fetchone()
            if not exist:
                db.execute(
                    "INSERT INTO webauthn_credentials (emp_id, credential_id, public_key, sign_count, device_name, aaguid) "
                    "VALUES (?, 'android_hw_device_001_test', 'sig_test', 0, 'Android Native Face ID', 'android-biometric')",
                    (emp_id,)
                )
                db.execute("UPDATE employees SET device_registered=1, face_registered=1 WHERE id=?", (emp_id,))
                db.commit()

        self._login(c, "E0012", "123456789")

        # Request challenge for punch
        ch_res = c.get("/api/android-biometric/challenge")
        ch_data = ch_res.get_json()
        self.assertIsNotNone(ch_data)
        self.assertTrue(ch_data["ok"])

        selfie = make_test_face(1)

        # Punch IN with android_biometric assertion
        punch_res = c.post("/api/punch", json={
            "kind": "in",
            "selfie": selfie,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 10,
            "android_biometric_assertion": {
                "id": "android_hw_device_001_test",
                "type": "android_biometric",
                "signature": "sig_test_hardware_signature",
                "challenge": ch_data["challenge"]
            }
        })
        p_data = punch_res.get_json()
        self.assertEqual(punch_res.status_code, 200)
        self.assertTrue(p_data["ok"])
        self.assertEqual(p_data["auth_method"], "android_biometric")
        self.assertTrue(p_data["face_verified"])

        # Check database attendance record
        with self.app.app_context():
            db = get_db()
            att = db.execute("SELECT * FROM attendance WHERE emp_id=? ORDER BY id DESC LIMIT 1", (emp_id,)).fetchone()
            self.assertIsNotNone(att["check_in"])
            self.assertEqual(att["auth_method"], "android_biometric")
            self.assertEqual(att["face_verified"], 1)
            self.assertEqual(att["device_credential_id"], "android_hw_device_001_test")

    def test_06_unauthenticated_punch_blocked_without_biometrics(self):
        """6. Rejects punch without phone biometric assertion (disables password/PIN/pattern)."""
        c = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')")
            db.execute("UPDATE employees SET active=1, block_reason=NULL WHERE emp_code='E0012'")
            db.commit()

        self._login(c, "E0012", "123456789")
        tok = self._get_csrf(c, "/punch")

        # Temporarily set app.config["TESTING"] = False to test live production enforcement
        self.app.config["TESTING"] = False
        try:
            selfie = make_test_face(1)
            res = c.post("/api/punch", json={
                "kind": "in",
                "selfie": selfie,
                "lat": 12.9716,
                "lng": 77.5946,
                "accuracy": 10
            }, headers={"X-CSRFToken": tok})
            data = res.get_json()
            self.assertEqual(res.status_code, 403)
            self.assertFalse(data["ok"])
            self.assertIn("biometric authentication required", data["error"].lower())
            self.assertIn("passwords, pins, and patterns are strictly disabled", data["error"].lower())
        finally:
            self.app.config["TESTING"] = True

    def test_07_device_not_registered_to_account_rejected_on_punch(self):
        """7. Punching with an unbound device is blocked with account binding error."""
        c = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE employees SET active=1, block_reason=NULL WHERE emp_code='EMP001'")
            db.execute("DELETE FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='EMP001')")
            db.commit()

        self._login(c, "EMP001", "123456789")

        # EMP001 gets challenge
        ch_res = c.get("/api/android-biometric/challenge")
        ch_data = ch_res.get_json()

        # EMP001 tries to submit punch assertion with an unbound device_id
        res = c.post("/api/punch", json={
            "kind": "in",
            "selfie": make_test_face(2),
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 10,
            "android_biometric_assertion": {
                "id": "unbound_hardware_device_999",  # not registered to EMP001
                "type": "android_biometric",
                "signature": "sig_test_hardware_signature",
                "challenge": ch_data["challenge"]
            }
        })
        data = res.get_json()
        self.assertEqual(res.status_code, 403)
        self.assertFalse(data["ok"])
        self.assertIn("not registered to your account", data["error"].lower())

    def test_08_selfie_image_access_and_owner_permissions(self):
        """8. Face images display properly for the registered employee and HR."""
        c = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE employees SET active=1, block_reason=NULL")
            db.commit()
            e = db.execute("SELECT id, emp_code, face_image FROM employees WHERE emp_code='EMP001'").fetchone()
            face_img_name = e["face_image"]
            self.assertIsNotNone(face_img_name)

        # Logged in employee can view their own face image
        self._login(c, "EMP001", "123456789")
        img_res = c.get(f"/api/selfie/{face_img_name}")
        self.assertEqual(img_res.status_code, 200)

        # Another non-HR employee is forbidden from viewing this face image
        self._login(c, "E0012", "123456789")
        bad_res = c.get(f"/api/selfie/{face_img_name}")
        self.assertEqual(bad_res.status_code, 403)

        # HR can view all employee face images
        self._login(c, "HR001", "123456789")
        hr_res = c.get(f"/api/selfie/{face_img_name}")
        self.assertEqual(hr_res.status_code, 200)

    def test_09_face_not_clear_dark_rejected_with_flag(self):
        """9. Dark/unclear face is rejected with face_not_clear flag and punch is not taken."""
        c = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE employees SET active=1, block_reason=NULL WHERE emp_code='E0012'")
            db.execute("DELETE FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')")
            db.commit()

        self._login(c, "E0012", "123456789")
        ch_res = c.get("/api/android-biometric/challenge")
        ch_data = ch_res.get_json()

        # Dark selfie (< 15 brightness)
        dark_buf = io.BytesIO()
        Image.new("RGB", (128, 128), (5, 5, 5)).save(dark_buf, "JPEG")
        dark_b64 = "data:image/jpeg;base64," + base64.b64encode(dark_buf.getvalue()).decode()

        res = c.post("/api/punch", json={
            "kind": "in",
            "selfie": dark_b64,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 10,
            "android_biometric_assertion": {
                "id": "android_hw_device_001_test",
                "type": "android_biometric",
                "signature": "sig_test_hardware_signature",
                "challenge": ch_data["challenge"]
            }
        })
        data = res.get_json()
        self.assertEqual(res.status_code, 400)
        self.assertFalse(data["ok"])
        self.assertTrue(data.get("face_not_clear"))
        self.assertIn("face not clear", data["error"].lower())

        # Verify no attendance record was created
        with self.app.app_context():
            db = get_db()
            att = db.execute("SELECT id FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')").fetchone()
            self.assertIsNone(att)

    def test_10_clear_face_punch_accepted(self):
        """10. Clear and bright face photo is accepted and attendance is recorded."""
        c = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE employees SET active=1, block_reason=NULL WHERE emp_code='E0012'")
            db.execute("DELETE FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')")
            db.commit()

        self._login(c, "E0012", "123456789")
        ch_res = c.get("/api/android-biometric/challenge")
        ch_data = ch_res.get_json()

        clear_selfie = make_test_face(1, brightness=1.0)
        res = c.post("/api/punch", json={
            "kind": "in",
            "selfie": clear_selfie,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 10,
            "android_biometric_assertion": {
                "id": "android_hw_device_001_test",
                "type": "android_biometric",
                "signature": "sig_test_hardware_signature",
                "challenge": ch_data["challenge"]
            }
        })
        data = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["auth_method"], "android_biometric")

        # Verify attendance record was successfully created
        with self.app.app_context():
            db = get_db()
            att = db.execute("SELECT id, check_in FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')").fetchone()
            self.assertIsNotNone(att)
            self.assertIsNotNone(att["check_in"])

    def test_11_user_sample_image_backlit_shadow_rejected_without_punch(self):
        """11. User sample backlit/shadowed image is rejected: Face Not Clear, punch blocked."""
        sample_path = r"C:/Users/ddamo/.gemini/antigravity/brain/1562887f-095c-4913-9c3b-4742010ba294/.user_uploaded/media_1792735228113_6ed5798c.jpg"
        if not os.path.exists(sample_path):
            self.skipTest("Sample image file not found in local artifacts")

        c = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE employees SET active=1, block_reason=NULL WHERE emp_code='E0012'")
            db.execute("DELETE FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')")
            db.commit()

        self._login(c, "E0012", "123456789")
        ch_res = c.get("/api/android-biometric/challenge")
        ch_data = ch_res.get_json()

        with open(sample_path, "rb") as f:
            sample_b64 = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()

        res = c.post("/api/punch", json={
            "kind": "in",
            "selfie": sample_b64,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 10,
            "android_biometric_assertion": {
                "id": "android_hw_device_001_test",
                "type": "android_biometric",
                "signature": "sig_test_hardware_signature",
                "challenge": ch_data["challenge"]
            }
        })
        data = res.get_json()
        self.assertEqual(res.status_code, 400)
        self.assertFalse(data["ok"])
        self.assertTrue(data.get("face_not_clear"))
        self.assertIn("face not clear", data["error"].lower())

        # Verify punch was NOT recorded in database
        with self.app.app_context():
            db = get_db()
            att = db.execute("SELECT id FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')").fetchone()
            self.assertIsNone(att)

    def test_12_strict_50m_geofence_enforced(self):
        """12. Strictly 50 m maximum radius allowed: >50m rejected, <=50m accepted."""
        c = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE employees SET active=1, block_reason=NULL WHERE emp_code='E0012'")
            db.execute("DELETE FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')")
            # Ensure office has strict 50m radius
            db.execute("UPDATE offices SET lat=12.971600, lng=77.594600, radius_m=50 WHERE department='Staff' OR id=1")
            db.commit()

        self._login(c, "E0012", "123456789")

        # 1. Test coordinate ~80m away (12.972300, 77.594600 is ~78 meters north)
        geo_out = c.post("/api/geo-check", json={"lat": 12.972350, "lng": 77.594600, "accuracy": 10}).get_json()
        self.assertTrue(geo_out["ok"])
        self.assertFalse(geo_out["inside"])
        self.assertFalse(geo_out["allowed"])
        self.assertEqual(geo_out["radius"], 50)
        self.assertGreater(geo_out["distance"], 50)

        # 2. Punch attempt outside 50m is rejected
        ch_res = c.get("/api/android-biometric/challenge")
        ch_data = ch_res.get_json()
        clear_selfie = make_test_face(1)
        res_fail = c.post("/api/punch", json={
            "kind": "in",
            "selfie": clear_selfie,
            "lat": 12.972350,
            "lng": 77.594600,
            "accuracy": 10,
            "android_biometric_assertion": {
                "id": "android_hw_device_001_test",
                "type": "android_biometric",
                "signature": "sig_test_hardware_signature",
                "challenge": ch_data["challenge"]
            }
        })
        self.assertEqual(res_fail.status_code, 400)
        fail_data = res_fail.get_json()
        self.assertFalse(fail_data["ok"])
        self.assertIn("allowed 50 m only", fail_data["error"])

        # 3. Coordinate within 50m (12.971750, 77.594600 is ~17 meters north) is allowed
        geo_in = c.post("/api/geo-check", json={"lat": 12.971750, "lng": 77.594600, "accuracy": 10}).get_json()
        self.assertTrue(geo_in["ok"])
        self.assertTrue(geo_in["inside"])
        self.assertTrue(geo_in["allowed"])
        self.assertEqual(geo_in["radius"], 50)
        self.assertLessEqual(geo_in["distance"], 50)

    def test_13_four_department_marked_locations(self):
        """13. All 4 departments (Staff, Production, Contract Operations, Accounts & Finance) have marked location points."""
        with self.app.app_context():
            db = get_db()
            offices = db.execute("SELECT name, department, radius_m, active FROM offices WHERE active=1").fetchall()
            dept_names = [o["department"] for o in offices if o["department"]]

            # Verify all 4 departments are represented
            self.assertIn("Staff", dept_names)
            self.assertIn("Production", dept_names)
            self.assertIn("Contract Operations", dept_names)
            self.assertIn("Accounts & Finance", dept_names)

            # Verify every department location has strict 50m radius
            for o in offices:
                self.assertLessEqual(o["radius_m"], 50)

    def test_14_user_covered_closed_camera_rejected_without_punch(self):
        """14. Covered camera lens or closed camera photo is strictly rejected: Face Not Clear modal triggered and punch blocked."""
        sample_path = r"C:/Users/ddamo/.gemini/antigravity/brain/1562887f-095c-4913-9c3b-4742010ba294/.user_uploaded/media_1793078720388_f1bfac9a.jpg"
        if not os.path.exists(sample_path):
            self.skipTest("Sample covered image file not found in local artifacts")

        c = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE employees SET active=1, block_reason=NULL WHERE emp_code='E0012'")
            db.execute("DELETE FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')")
            db.commit()

        self._login(c, "E0012", "123456789")
        ch_res = c.get("/api/android-biometric/challenge")
        ch_data = ch_res.get_json()

        with open(sample_path, "rb") as f:
            covered_b64 = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()

        res = c.post("/api/punch", json={
            "kind": "in",
            "selfie": covered_b64,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 10,
            "android_biometric_assertion": {
                "id": "android_hw_device_001_test",
                "type": "android_biometric",
                "signature": "sig_test_hardware_signature",
                "challenge": ch_data["challenge"]
            }
        })
        data = res.get_json()
        self.assertEqual(res.status_code, 400)
        self.assertFalse(data["ok"])
        self.assertTrue(data.get("face_not_clear"))
        self.assertIn("face not clear", data["error"].lower())
        self.assertIn("covered or closed", data["error"].lower())

        # Verify punch was NOT recorded in database
        with self.app.app_context():
            db = get_db()
            att = db.execute("SELECT id FROM attendance WHERE emp_id=(SELECT id FROM employees WHERE emp_code='E0012')").fetchone()
            self.assertIsNone(att)

