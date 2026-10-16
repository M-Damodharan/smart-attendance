"""SBG Smart Attendance - Biometric Face Recognition & Account Binding Security Tests.

Tests the strict security requirements:
1. One Face, One Employee ID (Duplicate face registration blocked).
2. Face must match the logged-in account.
3. Live face matching another employee's account is rejected with:
   "Face does not match this employee account. Please log in with your registered account."
4. Unrecognized face mismatch is rejected with:
   "Face verification failed. Live face does not match the registered face for this employee."
5. HR-assisted face reset and recovery workflow.
6. Server-side audit logging for all biometric events.
"""
import base64
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import date

import numpy as np
from PIL import Image, ImageEnhance

import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app
from app.db import get_db, init_db
from app.face_engine import (
    FACE_MATCH_THRESHOLD,
    check_duplicate_face,
    compute_similarity,
    extract_face_embedding,
    register_face_for_employee,
    reset_face_for_employee,
    verify_face_for_employee,
)


def make_test_face(person_id: int, brightness: float = 1.0) -> str:
    """Generates a reproducible synthetic facial image data URL with distinct geometry per person."""
    # Person-specific facial geometry: center coordinates and aspect ratios
    geometries = {
        1: (60, 56, 42, 52),  # Damodharan
        2: (70, 68, 54, 38),  # Staff
        3: (52, 72, 36, 44),  # Stranger
    }
    cx, cy, rx, ry = geometries.get(person_id, (64, 64, 45, 45))
    rng = np.random.default_rng(person_id * 100)
    y, x = np.mgrid[0:128, 0:128]
    dist = ((x - cx) / float(rx)) ** 2 + ((y - cy) / float(ry)) ** 2
    base = (np.clip(1.0 - np.sqrt(dist), 0, 1) * 200 + rng.integers(10, 50, (128, 128))).astype(np.uint8)
    img = Image.fromarray(base, mode="L").convert("RGB")
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class TestFaceSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmpdir, "test_face.db")
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
        cls.app.config["MIN_PUNCH_GAP_MIN"] = 0

        init_db(cls.app)

        with cls.app.app_context():
            db = get_db()
            db.execute("UPDATE offices SET lat=12.9716, lng=77.5946, radius_m=200 WHERE id=1")
            db.execute("UPDATE employees SET office_id=1, must_change_pw=0")
            db.commit()

        # Generate face test fixtures
        cls.face_damodharan = make_test_face(1)
        cls.face_damodharan_live = make_test_face(1, brightness=1.05)  # Same person, minor light change
        cls.face_staff = make_test_face(2)
        cls.face_stranger = make_test_face(3)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _get_csrf(self, client, url="/login"):
        res = client.get(url)
        import re
        txt = res.get_data(as_text=True)
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', txt)
        if not m:
            m = re.search(r'name="csrf-token"\s+content="([^"]+)"', txt)
        return m.group(1) if m else ""

    def _login(self, client, code, password):
        client.get("/logout")
        token = self._get_csrf(client, "/login")
        return client.post("/login", data={"emp_code": code, "password": password, "csrf_token": token}, follow_redirects=True)

    def test_01_one_face_one_account_registration(self):
        """1. One face, one employee ID:
        Register face A for Damodharan M (E0012).
        Attempt to register the same face for Employee Staff (EMP001).
        The duplicate registration MUST be rejected!
        """
        with self.app.app_context():
            db = get_db()
            e_damo = db.execute("SELECT id FROM employees WHERE emp_code='E0012'").fetchone()
            e_staff = db.execute("SELECT id FROM employees WHERE emp_code='EMP001'").fetchone()
            self.assertIsNotNone(e_damo)
            self.assertIsNotNone(e_staff)

            # Register face for Damodharan (E0012)
            ok, msg, _ = register_face_for_employee(db, e_damo["id"], self.face_damodharan, registered_by_id=1)
            self.assertTrue(ok, f"Registration for E0012 failed: {msg}")

            # Verify registered status in DB
            row = db.execute("SELECT face_registered, face_template FROM employees WHERE id=?", (e_damo["id"],)).fetchone()
            self.assertEqual(row["face_registered"], 1)
            self.assertIsNotNone(row["face_template"])

            # Attempt to register the SAME face for Employee Staff (EMP001)
            dup_ok, dup_msg, dup_emp = register_face_for_employee(db, e_staff["id"], self.face_damodharan, registered_by_id=1)
            self.assertFalse(dup_ok, "Duplicate face registration was incorrectly allowed!")
            self.assertIn("already registered", dup_msg.lower())
            self.assertEqual(dup_emp["emp_code"], "E0012")

            # Check audit log recorded the blocked duplicate
            audit_rec = db.execute(
                "SELECT * FROM audit_log WHERE action='FACE_DUPLICATE_REGISTRATION_BLOCKED' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(audit_rec)
            self.assertIn("E0012", audit_rec["details"])

    def test_02_face_match_logged_in_account_punch_succeeds(self):
        """2. Face must match the logged-in account:
        Damodharan logs into E0012 and punches in with his registered face.
        Punch MUST succeed with face_verified=1.
        """
        c = self.app.test_client()
        self._login(c, "E0012", "123456789")
        t = self._get_csrf(c, "/dashboard")

        ch = c.post("/api/challenge", headers={"X-CSRFToken": t}).get_json()
        self.assertTrue(ch["ok"])

        # Create motion frames using Damodharan's face
        frames = [self.face_damodharan, self.face_damodharan_live, self.face_damodharan]
        punch_res = c.post("/api/punch", json={
            "kind": "in",
            "token": ch["token"],
            "frames": frames,
            "selfie": self.face_damodharan_live,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 15
        }, headers={"X-CSRFToken": t})

        data = punch_res.get_json()
        self.assertEqual(punch_res.status_code, 200, f"Punch failed: {data}")
        self.assertTrue(data.get("ok"))

        # Verify database record
        with self.app.app_context():
            db = get_db()
            e_damo = db.execute("SELECT id FROM employees WHERE emp_code='E0012'").fetchone()
            att = db.execute("SELECT * FROM attendance WHERE emp_id=? ORDER BY id DESC LIMIT 1", (e_damo["id"],)).fetchone()
            self.assertIsNotNone(att)
            self.assertEqual(att["face_verified"], 1)
            self.assertGreaterEqual(att["face_similarity"], FACE_MATCH_THRESHOLD)

    def test_03_cross_account_face_punch_blocked(self):
        """3. Cross-Account Punch Rejected:
        Register distinct face for EMP001.
        Now, someone logs into EMP001 but uses Damodharan's face (E0012) to punch.
        The punch MUST be rejected with HTTP 403 and the exact required message:
        'Face does not match this employee account. Please log in with your registered account.'
        """
        with self.app.app_context():
            db = get_db()
            e_staff = db.execute("SELECT id FROM employees WHERE emp_code='EMP001'").fetchone()
            # Register Staff's own face
            ok, msg, _ = register_face_for_employee(db, e_staff["id"], self.face_staff, registered_by_id=1)
            self.assertTrue(ok)

        c = self.app.test_client()
        self._login(c, "EMP001", "123456789")
        t = self._get_csrf(c, "/dashboard")

        ch = c.post("/api/challenge", headers={"X-CSRFToken": t}).get_json()
        self.assertTrue(ch["ok"])

        # Try punching into EMP001 with Damodharan's face!
        frames = [self.face_damodharan, self.face_damodharan_live, self.face_damodharan]
        punch_res = c.post("/api/punch", json={
            "kind": "in",
            "token": ch["token"],
            "frames": frames,
            "selfie": self.face_damodharan,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 15
        }, headers={"X-CSRFToken": t})

        data = punch_res.get_json()
        self.assertEqual(punch_res.status_code, 403)
        self.assertFalse(data.get("ok"))
        self.assertEqual(
            data.get("error"),
            "Face does not match this employee account. Please log in with your registered account."
        )

        # Verify security breach audit log
        with self.app.app_context():
            db = get_db()
            audit_rec = db.execute(
                "SELECT * FROM audit_log WHERE action='FACE_ACCOUNT_MISMATCH' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(audit_rec)
            self.assertIn("SPOOF_ATTEMPT", audit_rec["details"])
            self.assertIn("E0012", audit_rec["details"])

    def test_04_stranger_face_punch_blocked(self):
        """4. Unrecognized Face Punch Rejected:
        Damodharan logs into E0012, but an unrecognized person (stranger) is in front of the camera.
        Punch MUST be rejected with HTTP 403 and message:
        'Face verification failed. Live face does not match the registered face for this employee.'
        """
        c = self.app.test_client()
        self._login(c, "E0012", "123456789")
        t = self._get_csrf(c, "/dashboard")

        ch = c.post("/api/challenge", headers={"X-CSRFToken": t}).get_json()
        self.assertTrue(ch["ok"])

        frames = [
            make_test_face(3, brightness=0.88),
            make_test_face(3, brightness=1.12),
            make_test_face(3, brightness=1.0)
        ]
        punch_res = c.post("/api/punch", json={
            "kind": "out",
            "token": ch["token"],
            "frames": frames,
            "selfie": self.face_stranger,
            "lat": 12.9716,
            "lng": 77.5946,
            "accuracy": 15
        }, headers={"X-CSRFToken": t})

        data = punch_res.get_json()
        self.assertEqual(punch_res.status_code, 403)
        self.assertFalse(data.get("ok"))
        self.assertIn("Face verification failed", data.get("error"))

    def test_05_hr_assisted_face_reset_recovery(self):
        """5. HR-assisted recovery:
        HR resets face registration for E0012.
        Template is cleared, and audit log records FACE_RESET.
        Then new face can be registered.
        """
        with self.app.app_context():
            db = get_db()
            e_damo = db.execute("SELECT id FROM employees WHERE emp_code='E0012'").fetchone()

            # Reset face
            ok = reset_face_for_employee(db, e_damo["id"], reset_by_id=1)
            self.assertTrue(ok)

            row = db.execute("SELECT face_registered, face_template FROM employees WHERE id=?", (e_damo["id"],)).fetchone()
            self.assertEqual(row["face_registered"], 0)
            self.assertIsNone(row["face_template"])

            audit_rec = db.execute(
                "SELECT * FROM audit_log WHERE action='FACE_RESET' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(audit_rec)
            self.assertIn("E0012", audit_rec["details"])

            # Re-register face succeeds
            ok2, msg2, _ = register_face_for_employee(db, e_damo["id"], self.face_damodharan_live, registered_by_id=1)
            self.assertTrue(ok2)


if __name__ == "__main__":
    unittest.main()
