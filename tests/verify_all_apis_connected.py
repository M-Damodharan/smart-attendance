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

class AllApisConnectedTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
        cls.client = cls.app.test_client()

        with cls.app.app_context():
            db = get_db()
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("INSERT OR IGNORE INTO employee_categories (id, name, code, work_hours, overtime_hours) VALUES (1, 'Staff', 'STAFF', 10.0, 0.0)")
            db.execute("INSERT OR IGNORE INTO shifts (id, name, start_time, end_time, grace_min, full_hours, half_hours) VALUES (1, 'General Shift', '09:00', '18:00', 15, 8.0, 4.0)")
            db.execute("INSERT OR REPLACE INTO offices (id, name, department, lat, lng, radius_m, active) VALUES (1, 'Main Office', 'Staff', 12.762733, 77.802695, 5, 1)")

            # Ensure admin
            db.execute("INSERT OR REPLACE INTO employees (id, emp_code, name, role, active, password_hash, must_change_pw) VALUES (1, 'HR001', 'HR Admin', 'hr', 1, 'hash', 0)")
            # Ensure employee
            db.execute("INSERT OR REPLACE INTO employees (id, emp_code, name, role, active, password_hash, must_change_pw, office_id, category_id, shift_id, department, device_registered, device_name) VALUES (2, 'EMP001', 'Employee One', 'employee', 1, 'hash', 0, 1, 1, 1, 'Staff', 1, 'Test Biometric Device')")
            
            # Ensure webauthn credential
            db.execute("INSERT OR REPLACE INTO webauthn_credentials (id, emp_id, credential_id, public_key, sign_count, device_name, aaguid, created_at) VALUES (1, 2, 'test_cred_webauthn_1', 'pk_test', 0, 'Test Biometric Device', 'test', '2026-11-03 09:00:00')")

            # Ensure sample attendance record for comments and missed punch
            db.execute("INSERT OR REPLACE INTO attendance (id, emp_id, date, shift_id, check_in, check_out, in_lat, in_lng, in_dist, in_geo_ok, work_hours, ot_hours, status, late, face_verified) VALUES (1, 2, '2026-11-03', 1, '2026-11-03 09:05:00', NULL, 12.762733, 77.802695, 2.0, 1, 0, 0, 'Present', 0, 1)")

            # Ensure sample late coming request
            db.execute("INSERT OR REPLACE INTO late_coming_requests (id, att_id, emp_id, date, shift_id, check_in, late_minutes, work_hours, shortage_hours, reason, status) VALUES (1, 1, 2, '2026-11-03', 1, '09:25:00', 25, 7.5, 0.5, 'NH44 traffic', 'Pending')")

            # Ensure sample Sunday working assignment
            db.execute("INSERT OR REPLACE INTO sunday_working (id, emp_id, sunday_date, shift_id, duty_type, check_in, check_out, work_hours, ot_hours, status) VALUES (1, 2, '2026-11-08', 1, 'Full Day', '09:00:00', '18:00:00', 8.0, 0.0, 'Pending Approval')")

            # Ensure sample AI face alert
            db.execute("INSERT OR REPLACE INTO ai_face_alerts (id, date, emp_id, target_emp_id, similarity, selfie_path, target_selfie_path, alert_type, status, details) VALUES (1, '2026-11-03', 2, 1, 0.88, 'test.jpg', 'target.jpg', 'BUDDY_PUNCH', 'BLOCKED', 'Cross-account match')")

            # Ensure system settings
            db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '100')")
            db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")
            db.execute("PRAGMA foreign_keys = ON")
            db.commit()

    def _login_emp(self):
        with self.client.session_transaction() as sess:
            sess["uid"] = 2
            sess["emp_code"] = "EMP001"
            sess["role"] = "employee"
            sess["name"] = "Employee One"

    def _login_hr(self):
        with self.client.session_transaction() as sess:
            sess["uid"] = 1
            sess["emp_code"] = "HR001"
            sess["role"] = "hr"
            sess["name"] = "HR Admin"

    # --- 1. System Health API ---
    def test_01_healthz_api(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")

    # --- 2. GPS & Geofence APIs ---
    def test_02_geo_my_location_api(self):
        res = self.client.get("/api/geo/my-location")
        self.assertIn(res.status_code, (200, 400))
        self.assertNotIn("500", str(res.status_code))

    def test_03_geo_check_api_connected(self):
        self._login_emp()
        res = self.client.post("/api/geo-check", json={
            "lat": 12.762733,
            "lng": 77.802695,
            "accuracy": 15.0
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["inside"])
        self.assertTrue(data["allowed"])
        self.assertEqual(data["rules"], "5 meters inside accepted")

    # --- 3. Biometric & Challenge APIs ---
    def test_04_challenge_api_connected(self):
        self._login_emp()
        res = self.client.post("/api/challenge")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("token", data)
        self.assertIn("prompts", data)

    def test_05_android_biometric_challenge_api(self):
        self._login_emp()
        res = self.client.get("/api/android-biometric/challenge")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("challenge", data)

    def test_06_android_biometric_register_device_api(self):
        self._login_emp()
        ch_res = self.client.get("/api/android-biometric/challenge")
        ch = ch_res.get_json()["challenge"]
        res = self.client.post("/api/android-biometric/register-device", json={
            "device_id": "test_cred_android_999",
            "signature": "sig_android_test",
            "challenge": ch
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])

    def test_07_webauthn_status_api(self):
        self._login_emp()
        res = self.client.get("/api/webauthn/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("device_registered", data)

    def test_08_webauthn_register_challenge_api(self):
        self._login_emp()
        res = self.client.post("/api/webauthn/register/challenge")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("options", data)

    def test_09_webauthn_punch_challenge_api(self):
        self._login_emp()
        res = self.client.post("/api/webauthn/punch/challenge")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("options", data)

    # --- 4. Notification APIs ---
    def test_10_notifications_api(self):
        self._login_emp()
        res = self.client.get("/api/notifications")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("notifications", data)

    def test_11_notifications_read_all_api(self):
        self._login_emp()
        res = self.client.post("/api/notifications/read-all")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])

    # --- 5. Comment System APIs ---
    def test_12_comments_api(self):
        self._login_emp()
        res_post = self.client.post("/api/comments", json={
            "entity_type": "attendance",
            "entity_id": 1,
            "content": "Test API comment"
        })
        self.assertEqual(res_post.status_code, 200)
        self.assertTrue(res_post.get_json()["ok"])

        res_get = self.client.get("/api/comments/attendance/1")
        self.assertEqual(res_get.status_code, 200)
        data = res_get.get_json()
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["comments"], list)

    # --- 6. AI Assistant API ---
    def test_13_ai_chat_api(self):
        self._login_hr()
        res = self.client.post("/api/ai/chat", json={
            "message": "Who is absent today?"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("reply", data)

    # --- 7. Power BI Analytics API ---
    def test_14_powerbi_analytics_api(self):
        self._login_hr()
        res = self.client.get("/hr/api/powerbi-analytics?period=monthly")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success", False))
        self.assertIn("kpis", data)
        self.assertIn("departments", data)
        self.assertIn("monthly", data)
        self.assertIn("weekly", data)

    # --- 8. Cloud Backup & Sync APIs ---
    def test_15_cloud_backup_telemetry_api(self):
        self._login_hr()
        res = self.client.get("/hr/cloud-backup-sync/api/telemetry")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("active_employees", data)

    def test_16_cloud_backup_create_api(self):
        self._login_hr()
        res = self.client.post("/hr/cloud-backup-sync/api/backup")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("backup_id", data)

    def test_17_cloud_backup_verify_api(self):
        self._login_hr()
        res = self.client.get("/hr/cloud-backup-sync/api/verify/test_backup_001")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("backup_id", data)

    # --- 9. Account Switcher APIs ---
    def test_18_switchable_accounts_api(self):
        self._login_hr()
        res = self.client.get("/auth/switchable-accounts")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("accounts", data)

    def test_19_switch_account_api(self):
        self._login_hr()
        res = self.client.post("/auth/switch-account", json={
            "target_id": 2
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("Switched account", data["message"])
        self.assertIn("redirect", data)

    # --- 10. Late Coming APIs ---
    def test_20_late_coming_submit_api(self):
        self._login_emp()
        res = self.client.post("/late-coming/submit", data={
            "reason": "Traffic delay on NH44",
            "attendance_id": "1"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    def test_21_late_coming_decide_api(self):
        self._login_hr()
        res = self.client.post("/hr/late-coming/decide/1", data={
            "status": "Approved",
            "hr_remarks": "Approved with grace period"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    # --- 11. Sunday Working APIs ---
    def test_22_sunday_working_assign_api(self):
        self._login_hr()
        res = self.client.post("/hr/sunday-working/assign", data={
            "emp_ids": ["2"],
            "sunday_date": "2026-11-08",
            "shift_id": "1",
            "duty_type": "Full Day",
            "remarks": "Urgent production run"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    def test_23_sunday_working_record_punch_api(self):
        self._login_hr()
        res = self.client.post("/hr/sunday-working/record-punch", data={
            "emp_id": "2",
            "sunday_date": "2026-11-08",
            "check_in": "09:00",
            "check_out": "17:30",
            "shift_id": "1",
            "duty_type": "Full Day"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    def test_24_sunday_working_approve_api(self):
        self._login_hr()
        res = self.client.post("/hr/sunday-working/approve/1", data={
            "action": "Approved",
            "hr_remarks": "Verified and approved"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    # --- 12. Payroll APIs ---
    def test_25_payroll_setup_api(self):
        self._login_hr()
        res = self.client.post("/hr/payroll/setup", data={
            "emp_id": ["2"],
            "basic_2": "28000",
            "quota_2": "2",
            "days_2": "30",
            "method_2": "30_days",
            "ot_rate_2": "150",
            "sunday_rate_2": "1000",
            "bonus_2": "500",
            "deduction_2": "0"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    def test_26_payroll_calculate_api(self):
        self._login_hr()
        res = self.client.post("/hr/payroll/calculate", data={
            "month": "2026-11"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    # --- 13. Missed Punch Auto-Resolve API ---
    def test_27_missed_punch_resolve_api(self):
        self._login_hr()
        res = self.client.post("/hr/missed-punches/resolve", data={
            "att_id": "1",
            "resolution_action": "shift_end",
            "manager_notes": "Punch auto-resolved by verified shift end time"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    # --- 14. Face Security Audit Scan & Resolve APIs ---
    def test_28_face_security_audit_scan_api(self):
        self._login_hr()
        res = self.client.post("/hr/face-security-audit/scan", data={
            "date": "2026-11-03"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    def test_29_face_security_alert_resolve_api(self):
        self._login_hr()
        res = self.client.post("/hr/face-security-audit/resolve/1", data={
            "action": "unlock",
            "notes": "Verified authentic employee"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

    # --- 15. Service Worker & PWA APK APIs ---
    def test_30_service_worker_route(self):
        res = self.client.get("/sw.js")
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/javascript", res.headers.get("Content-Type", ""))

if __name__ == "__main__":
    unittest.main()
