"""Test Suite for Dynamic Multi-Theme System, UI/UX Enhancements, and Responsive Auto-Fit Layouts.

Verifies:
1. Theme Settings page (/theme and /hr/theme) renders properly for HR and Employees.
2. All 8 ready-made themes are supported and validated.
3. Light mode and Dark mode toggles and state tracking.
4. Backend persistence of user theme preferences in user_preferences table.
5. Custom color palette builder and JSON storage.
6. Navigation links, topbar theme palette button, and profile modal shortcuts.
7. Responsive auto-fit table and container rules in style.css.
"""
import json
import unittest

from app import create_app
from app.db import get_db
from app.api import VALID_THEMES


class TestThemeAndResponsiveEnhancements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = cls.app.test_client()

    def _login_hr(self):
        with self.client.session_transaction() as sess:
            sess["uid"] = 1
            sess["role"] = "hr"
            sess["name"] = "HR Admin"
            sess["csrf"] = "testcsrf"

    def _login_emp(self):
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT id, name FROM employees WHERE role='employee' LIMIT 1").fetchone()
            emp_id = emp["id"] if emp else 2
            emp_name = emp["name"] if emp else "Employee"
        with self.client.session_transaction() as sess:
            sess["uid"] = emp_id
            sess["role"] = "employee"
            sess["name"] = emp_name
            sess["csrf"] = "testcsrf"
        return emp_id

    def test_01_theme_options_specification_completeness(self):
        """All 8 requested themes must be recognized in the theme registry."""
        required_themes = [
            "default_blue",
            "modern_purple",
            "emerald_green",
            "ocean_teal",
            "dark_premium",
            "midnight_black",
            "rose_pink",
            "orange_modern"
        ]
        for t in required_themes:
            self.assertIn(t, VALID_THEMES, f"Theme {t} must be present in VALID_THEMES")

    def test_02_theme_page_access_control(self):
        """Unauthenticated requests redirect to login; logged-in users access /theme directly."""
        # Unauthenticated
        res = self.client.get("/theme", follow_redirects=False)
        self.assertIn(res.status_code, (302, 303))
        self.assertIn("/login", res.headers.get("Location", ""))

        # Logged in HR
        self._login_hr()
        res_hr = self.client.get("/theme")
        self.assertEqual(res_hr.status_code, 200)
        self.assertIn(b"Theme &amp; Appearance Settings", res_hr.data)
        self.assertIn(b"Default Professional Blue", res_hr.data)
        self.assertIn(b"Modern Purple", res_hr.data)
        self.assertIn(b"Emerald Green", res_hr.data)
        self.assertIn(b"Ocean Teal", res_hr.data)
        self.assertIn(b"Dark Premium", res_hr.data)
        self.assertIn(b"Midnight Black", res_hr.data)
        self.assertIn(b"Rose Pink", res_hr.data)
        self.assertIn(b"Orange Modern", res_hr.data)

        # /hr/theme redirects or loads
        res_hr_theme = self.client.get("/hr/theme", follow_redirects=True)
        self.assertEqual(res_hr_theme.status_code, 200)
        self.assertIn(b"Theme &amp; Appearance Settings", res_hr_theme.data)

        # Logged in Employee
        self._login_emp()
        res_emp = self.client.get("/theme")
        self.assertEqual(res_emp.status_code, 200)
        self.assertIn(b"Theme &amp; Appearance Settings", res_emp.data)

    def test_03_theme_api_save_and_database_persistence(self):
        """Test saving ready-made theme and mode to user_preferences table via API."""
        self._login_hr()

        # Save Emerald Green in Dark Mode
        res = self.client.post("/api/theme/save", json={
            "theme_name": "emerald_green",
            "theme_mode": "dark"
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("theme_name"), "emerald_green")
        self.assertEqual(data.get("theme_mode"), "dark")

        # Verify database record
        with self.app.app_context():
            db = get_db()
            row = db.execute("SELECT * FROM user_preferences WHERE user_id=1").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["theme_name"], "emerald_green")
            self.assertEqual(row["theme_mode"], "dark")

        # GET /api/theme returns saved preferences
        res_get = self.client.get("/api/theme")
        self.assertEqual(res_get.status_code, 200)
        get_data = res_get.get_json()
        self.assertEqual(get_data.get("theme_name"), "emerald_green")
        self.assertEqual(get_data.get("theme_mode"), "dark")

    def test_04_custom_colors_builder_persistence(self):
        """Test saving custom hex palette to user_preferences table."""
        self._login_hr()
        custom_palette = {
            "primary": "#4f46e5",
            "secondary": "#06b6d4",
            "bg": "#0f172a",
            "card": "#1e293b",
            "sidebar": "#1e293b",
            "text": "#f8fafc"
        }

        res = self.client.post("/api/theme/save", json={
            "theme_name": "custom",
            "theme_mode": "dark",
            "custom_colors": custom_palette
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("theme_name"), "custom")
        self.assertEqual(data.get("custom_colors"), custom_palette)

        with self.app.app_context():
            db = get_db()
            row = db.execute("SELECT * FROM user_preferences WHERE user_id=1").fetchone()
            self.assertIsNotNone(row)
            saved_colors = json.loads(row["custom_colors"])
            self.assertEqual(saved_colors["primary"], "#4f46e5")

    def test_05_navigation_and_ui_entrypoints(self):
        """Verify that UI contains Theme Palette button, Nav tabs, and Profile shortcuts."""
        self._login_hr()
        res = self.client.get("/hr/dashboard")
        self.assertEqual(res.status_code, 200)

        # Topbar palette button
        self.assertIn(b'id="theme-palette-btn"', res.data)
        self.assertIn(b'href="/theme"', res.data)

        # Nav bar theme link
        self.assertIn(b'<span>Themes</span>', res.data)

        # Profile modal contains Theme customize link
        self.assertIn(b'Customize Theme', res.data)

        # HR Settings contains Theme & Appearance card
        res_settings = self.client.get("/hr/settings")
        self.assertEqual(res_settings.status_code, 200)
        self.assertIn(b'id="theme-appearance"', res_settings.data)
        self.assertIn(b'Application Themes &amp; UI Customization', res_settings.data)

    def test_06_css_typography_and_responsive_rules(self):
        """Verify style.css includes modern fonts, auto-fit containers, and 8 theme rules."""
        with open("app/static/css/style.css", "r", encoding="utf-8") as f:
            css = f.read()

        # Font system
        self.assertIn("Plus Jakarta Sans", css)
        self.assertIn("--font-sans", css)

        # All 8 color theme selectors
        for t in ["default_blue", "modern_purple", "emerald_green", "ocean_teal", "dark_premium", "midnight_black", "rose_pink", "orange_modern"]:
            self.assertIn(f'[data-color-theme="{t}"]', css, f"Missing CSS selector for theme {t}")

        # Responsive auto-fit table & container rules
        self.assertIn(".tablewrap", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("@media (max-width: 1024px)", css)
        self.assertIn("@media (max-width: 768px)", css)


if __name__ == "__main__":
    unittest.main()
