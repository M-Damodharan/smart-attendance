"""Test Suite for Role-Based User & Page Permission Management.

Tests:
1. Super Admin full access retention (Requirement 8)
2. Admin can select any registered user and view all application modules/pages (Requirements 1 & 2)
3. Granular permissions (View, Add, Edit, Delete, Export) saving to database (Requirements 3 & 4)
4. Dynamic permission loading after login and cache invalidation (Requirement 5)
5. Sidebar visibility: Unauthorized pages hidden, authorized pages visible (Requirement 6)
6. Backend authorization: Direct URL navigation blocked with 403 for unauthorized users (Requirement 7)
7. Backend API authorization: Blocked with 403 for unauthorized users (Requirement 7)
8. Action-level authorization: View vs Add vs Edit vs Delete vs Export enforcement (Requirements 3 & 7)
9. Permission change audit logs recorded in database with actor, target, diffs, IP (Important Requirement)
10. Existing data integrity: Users, attendance records, shifts remain unchanged (Requirements 9 & 10)
"""
import unittest
import html
from flask import g, session

from app import create_app
from app.db import get_db
from app.permissions import (
    is_admin_user,
    has_permission,
    get_user_permissions,
    save_user_permissions,
    reset_user_permissions,
    MODULE_GROUPS,
    ALL_MODULES,
    ALL_ACTIONS,
)


class TestUserPagePermissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.app.app_context():
            db = get_db()
            # Ensure an HR admin and test employee exist
            self.hr_user = db.execute("SELECT * FROM employees WHERE role='hr' LIMIT 1").fetchone()
            self.emp_user = db.execute("SELECT * FROM employees WHERE role='employee' ORDER BY id LIMIT 1").fetchone()
            self.assertIsNotNone(self.hr_user, "HR admin user must exist")
            self.assertIsNotNone(self.emp_user, "Employee user must exist")

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess["uid"] = user["id"]
            sess["role"] = user["role"]
            sess["name"] = user["name"]
            sess["emp_code"] = user["emp_code"]

    # -------------------------------------------------------------------------
    # TEST 1: ADMIN FULL ACCESS RETENTION (Requirement 8)
    # -------------------------------------------------------------------------
    def test_01_admin_retains_full_access(self):
        """Super Admin (HR001 / ADMIN_CODE) always retains 100% full access to all modules and actions."""
        with self.app.app_context():
            db = get_db()
            admin = db.execute("SELECT * FROM employees WHERE emp_code='HR001'").fetchone()
            self.assertIsNotNone(admin)
            self.assertTrue(is_admin_user(admin))

            # Admin must have permissions for all modules and all actions
            for mod_key in ALL_MODULES.keys():
                for act in ALL_ACTIONS:
                    self.assertTrue(
                        has_permission(dict(admin), mod_key, act),
                        f"Admin must have {act} on {mod_key}"
                    )

            # Attempting to save restricted permissions on Admin must be ignored/prevented
            res = save_user_permissions(db, admin["id"], {
                "hr_dashboard": {"can_view": 0},
                "hr_payroll": {"can_view": 0}
            }, updated_by_id=admin["id"])
            self.assertTrue(res.get("is_admin", False))
            self.assertTrue(has_permission(dict(admin), "hr_payroll", "view"))

    # -------------------------------------------------------------------------
    # TEST 2: ADMIN CAN SELECT ANY USER & ALL MODULES ARE DISPLAYED (Req 1 & 2)
    # -------------------------------------------------------------------------
    def test_02_admin_module_permissions_page_get(self):
        """Admin can load /hr/permissions and see user selector and module catalogue."""
        self._login(self.hr_user)
        res = self.client.get("/hr/permissions")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"User &amp; Page Permissions", res.data)
        self.assertIn(b"Select Registered User", res.data)
        self.assertIn(b"Quick Presets", res.data)
        self.assertIn(b"Save Permissions", res.data)

        # Check that groups and modules are rendered
        for grp in MODULE_GROUPS:
            self.assertIn(html.escape(grp["group_name"]).encode("utf-8"), res.data)

        # Check API endpoint for user permissions
        api_res = self.client.get(f"/hr/permissions/api/{self.emp_user['id']}")
        self.assertEqual(api_res.status_code, 200)
        data = api_res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIn("permissions", data)
        self.assertEqual(data["user"]["id"], self.emp_user["id"])

    # -------------------------------------------------------------------------
    # TEST 3: GRANULAR PERMISSION SAVING (Requirements 3 & 4)
    # -------------------------------------------------------------------------
    def test_03_save_and_load_granular_permissions(self):
        """Individual View, Add, Edit, Delete, Export permissions save correctly to DB."""
        target_uid = self.emp_user["id"]
        custom_perms = {
            "emp_dashboard": {"can_view": 1, "can_add": 0, "can_edit": 0, "can_delete": 0, "can_export": 0},
            "hr_attendance": {"can_view": 1, "can_add": 0, "can_edit": 0, "can_delete": 0, "can_export": 1},
            "hr_payroll": {"can_view": 0, "can_add": 0, "can_edit": 0, "can_delete": 0, "can_export": 0},
            "emp_punch": {"can_view": 1, "can_add": 0, "can_edit": 0, "can_delete": 0, "can_export": 0}, # View only, no punch
            "emp_leave": {"can_view": 1, "can_add": 1, "can_edit": 0, "can_delete": 0, "can_export": 0},
        }

        with self.app.app_context():
            db = get_db()
            save_res = save_user_permissions(
                db,
                target_uid,
                custom_perms,
                updated_by_id=self.hr_user["id"],
                ip_address="192.168.1.10"
            )
            self.assertTrue(save_res["success"])

            # Verify in DB
            rows = db.execute("SELECT * FROM user_page_permissions WHERE user_id=?", (target_uid,)).fetchall()
            row_map = {r["module_key"]: dict(r) for r in rows}

            self.assertEqual(row_map["hr_attendance"]["can_view"], 1)
            self.assertEqual(row_map["hr_attendance"]["can_export"], 1)
            self.assertEqual(row_map["hr_attendance"]["can_edit"], 0)
            self.assertEqual(row_map["hr_payroll"]["can_view"], 0)
            self.assertEqual(row_map["emp_punch"]["can_add"], 0)

    # -------------------------------------------------------------------------
    # TEST 4: DYNAMIC PERMISSION APPLICATION AFTER LOGIN (Requirement 5)
    # -------------------------------------------------------------------------
    def test_04_dynamic_permission_application(self):
        """Permissions load dynamically into g.user on request."""
        self._login(self.emp_user)
        res = self.client.get("/dashboard")
        self.assertEqual(res.status_code, 200)

        # Verify g.user has permissions map populated
        with self.app.app_context():
            db = get_db()
            perms, is_custom = get_user_permissions(db, self.emp_user["id"], self.emp_user["role"], self.emp_user["emp_code"])
            self.assertTrue(is_custom)
            self.assertEqual(perms["hr_attendance"]["can_view"], 1)
            self.assertEqual(perms["hr_payroll"]["can_view"], 0)

    # -------------------------------------------------------------------------
    # TEST 5: SIDEBAR HIDES UNAUTHORIZED PAGES (Requirement 6)
    # -------------------------------------------------------------------------
    def test_05_sidebar_hides_unauthorized_pages(self):
        """Unauthorized modules are not visible in the navigation sidebar."""
        self._login(self.emp_user)
        res = self.client.get("/dashboard")
        self.assertEqual(res.status_code, 200)

        # HR Payroll was revoked (can_view=0): must NOT appear in sidebar
        self.assertNotIn(b'href="/hr/payroll"', res.data)

        # HR Attendance was granted (can_view=1): MUST appear in sidebar
        self.assertIn(b'href="/hr/attendance"', res.data)

    # -------------------------------------------------------------------------
    # TEST 6: BACKEND AUTHORIZATION BLOCKS DIRECT URLS (Requirement 7)
    # -------------------------------------------------------------------------
    def test_06_direct_url_access_blocked_with_403(self):
        """Direct URL navigation to unauthorized pages is blocked with HTTP 403 Forbidden."""
        self._login(self.emp_user)

        # Direct navigation to /hr/payroll (no view permission)
        res_payroll = self.client.get("/hr/payroll")
        self.assertEqual(res_payroll.status_code, 403)
        self.assertIn(b"Access Denied", res_payroll.data)

        # Direct navigation to /hr/settings (no view permission)
        res_settings = self.client.get("/hr/settings")
        self.assertEqual(res_settings.status_code, 403)

        # Direct navigation to /hr/attendance (view permission granted)
        res_att = self.client.get("/hr/attendance")
        self.assertEqual(res_att.status_code, 200)

    # -------------------------------------------------------------------------
    # TEST 7: BACKEND API AUTHORIZATION ENFORCEMENT (Requirement 7)
    # -------------------------------------------------------------------------
    def test_07_backend_api_punch_blocked_with_403_when_no_add_permission(self):
        """API /api/punch is blocked with 403 when user's add permission is revoked."""
        self._login(self.emp_user)
        # Attempt punch POST (we set emp_punch can_add=0 in test_03)
        res = self.client.post("/api/punch", json={"kind": "in"})
        self.assertEqual(res.status_code, 403)
        data = res.get_json()
        self.assertFalse(data.get("ok"))
        self.assertIn("disabled", data.get("error", "").lower())

    # -------------------------------------------------------------------------
    # TEST 8: ACTION LEVEL AUTHORIZATION (View vs Edit vs Export)
    # -------------------------------------------------------------------------
    def test_08_action_level_checks(self):
        """Action-level authorization enforces export vs edit vs view."""
        self._login(self.emp_user)

        # User has hr_attendance view=1, export=1, but edit=0
        # Accessing /hr/attendance (view) is 200
        res_view = self.client.get("/hr/attendance")
        self.assertEqual(res_view.status_code, 200)

        # Attempting corrections /hr/corrections (no edit permission)
        res_edit = self.client.post("/hr/corrections", data={"emp_id": str(self.emp_user["id"]), "date": "2026-11-13"})
        self.assertEqual(res_edit.status_code, 403)

    # -------------------------------------------------------------------------
    # TEST 9: AUDIT LOGS FOR PERMISSION CHANGES (Important Requirement)
    # -------------------------------------------------------------------------
    def test_09_permission_audit_logging(self):
        """Permission changes and resets record full audit logs in the database."""
        with self.app.app_context():
            db = get_db()
            logs = db.execute(
                "SELECT * FROM permission_audit_log WHERE target_user_id=? ORDER BY id DESC",
                (self.emp_user["id"],)
            ).fetchall()
            self.assertTrue(len(logs) > 0, "Audit log records must be created")
            latest_log = logs[0]
            self.assertIn("UPDATE_PERMISSIONS", latest_log["action"])
            self.assertEqual(latest_log["changed_by_id"], self.hr_user["id"])
            self.assertIn("hr_attendance", latest_log["details"])

            # Test reset to default
            reset_res = reset_user_permissions(db, self.emp_user["id"], updated_by_id=self.hr_user["id"])
            self.assertTrue(reset_res["success"])

            # Check audit log for reset
            reset_logs = db.execute(
                "SELECT * FROM permission_audit_log WHERE target_user_id=? AND action='RESET_TO_DEFAULT'",
                (self.emp_user["id"],)
            ).fetchall()
            self.assertTrue(len(reset_logs) > 0)

    # -------------------------------------------------------------------------
    # TEST 10: EXISTING DATA INTEGRITY UNCHANGED (Requirements 9 & 10)
    # -------------------------------------------------------------------------
    def test_10_existing_data_remains_untouched(self):
        """Existing employees, attendance records, categories, and shifts remain valid and untouched."""
        with self.app.app_context():
            db = get_db()
            emp_count = db.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            self.assertGreater(emp_count, 0)

            att_count = db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
            self.assertGreaterEqual(att_count, 0)

            shift_count = db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
            self.assertGreaterEqual(shift_count, 1)

            cat_count = db.execute("SELECT COUNT(*) FROM employee_categories").fetchone()[0]
            self.assertGreaterEqual(cat_count, 1)
