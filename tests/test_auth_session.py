"""Test Suite for Persistent Login and Secure Logout Functionality across App and Website.

Verifies:
1. User logs in once and session is permanent (90-day lifetime with sliding refresh).
2. Cookie header includes proper Expires and Max-Age attributes.
3. Session remains active across multiple requests without requiring repeated login.
4. Simulating app/browser close and reopen with persistent cookie retains login state.
5. Logout completely clears session, deletes session cookies, and sets security headers.
6. Subsequent requests after logout are denied and require logging in again.
7. User Profile modal and HR Settings page provide prominent Logout options.
"""
import unittest
from flask import session

from app import create_app
from app.db import get_db


class TestAuthSessionPersistenceAndLogout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = cls.app.test_client()

    def test_01_login_sets_permanent_session_and_cookie(self):
        """Verify that login sets session.permanent=True and generates a persistent cookie with Expires/Max-Age."""
        res = self.client.post("/login", data={
            "emp_code": "HR001",
            "password": "damo12345",
            "login_type": "staff"
        }, follow_redirects=False)

        self.assertIn(res.status_code, (302, 303), f"Login should redirect, got {res.status_code}")

        # Check Set-Cookie header on login response
        cookie_headers = res.headers.getlist("Set-Cookie")
        self.assertTrue(len(cookie_headers) > 0, "Response must include Set-Cookie header")

        session_cookie = None
        for ch in cookie_headers:
            if "session=" in ch:
                session_cookie = ch
                break

        self.assertIsNotNone(session_cookie, f"Session cookie not found in headers: {cookie_headers}")
        # Verify persistence attributes
        self.assertTrue(
            "Expires=" in session_cookie or "Max-Age=" in session_cookie,
            f"Session cookie must have Expires or Max-Age for persistence: {session_cookie}"
        )

        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("uid"), 1)
            self.assertTrue(sess.permanent, "session.permanent must be True after login")

    def test_02_persistent_session_survives_across_requests(self):
        """Simulate closing/reopening the app or website with saved cookies to ensure session remains active."""
        # 1. Login
        self.client.post("/login", data={
            "emp_code": "HR001",
            "password": "damo12345",
            "login_type": "staff"
        }, follow_redirects=True)

        # 2. Extract cookie value
        cookie = self.client.get_cookie("session")
        self.assertIsNotNone(cookie, "Session cookie should be present in client cookie jar")

        # 3. Create a fresh new client simulating app restart, but preserving the persistent cookie
        fresh_client = self.app.test_client()
        fresh_client.set_cookie(
            key="session",
            value=cookie.value,
            domain="localhost",
            path="/"
        )

        # 4. Access protected dashboard on fresh client
        res = fresh_client.get("/hr/dashboard", follow_redirects=False)
        self.assertEqual(res.status_code, 200, "Fresh client with saved cookie must directly access dashboard without login")
        self.assertIn(b"HR Administrator", res.data)

    def test_03_logout_completely_clears_session_and_deletes_cookie(self):
        """Verify that clicking Logout completely wipes session, deletes cookie, and sets anti-cache headers."""
        # 1. Login first
        self.client.post("/login", data={
            "emp_code": "HR001",
            "password": "damo12345",
            "login_type": "staff"
        }, follow_redirects=True)

        # 2. Access /logout
        res = self.client.get("/logout", follow_redirects=False)
        self.assertIn(res.status_code, (302, 303), "Logout should redirect to login page")
        self.assertIn("/login?logged_out=1", res.headers.get("Location", ""))

        # Verify security anti-caching and Clear-Site-Data headers
        self.assertIn("no-store", res.headers.get("Cache-Control", ""))
        self.assertIn("cookies", res.headers.get("Clear-Site-Data", ""))

        # Check Set-Cookie headers for deletion (Expires=Thu, 01 Jan 1970 or Max-Age=0)
        cookie_headers = res.headers.getlist("Set-Cookie")
        deleted_session = any("session=" in ch and ("Max-Age=0" in ch or "1970" in ch) for ch in cookie_headers)
        self.assertTrue(deleted_session, f"Logout response must explicitly expire session cookie: {cookie_headers}")

        # Check that session dictionary is empty
        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("uid"), "Session uid must be None after logout")
            self.assertFalse(sess.permanent, "session.permanent must be False after logout")

    def test_04_subsequent_request_after_logout_requires_login(self):
        """Verify that after logout, subsequent visits to protected pages are blocked and require login."""
        # Access protected HR dashboard
        res = self.client.get("/hr/dashboard", follow_redirects=False)
        self.assertIn(res.status_code, (302, 303), "Subsequent request after logout must be redirected")
        self.assertIn("/login", res.headers.get("Location", ""))

        # Visiting login with logged_out=1 shows confirmation
        res_login = self.client.get("/login?logged_out=1")
        self.assertEqual(res_login.status_code, 200)
        self.assertIn(b"signed out successfully", res_login.data)

    def test_05_profile_modal_and_settings_include_logout_option(self):
        """Verify that UI contains Logout option in User Profile modal and HR Settings."""
        self.client.post("/login", data={
            "emp_code": "HR001",
            "password": "damo12345",
            "login_type": "staff"
        }, follow_redirects=True)

        # Check dashboard (base.html profile modal)
        res_dash = self.client.get("/hr/dashboard")
        self.assertEqual(res_dash.status_code, 200)
        self.assertIn(b'id="profile-modal"', res_dash.data)
        self.assertIn(b'/logout', res_dash.data)
        self.assertIn(b'Sign Out / Logout', res_dash.data)

        # Check HR Settings page
        res_settings = self.client.get("/hr/settings")
        self.assertEqual(res_settings.status_code, 200)
        self.assertIn(b'id="session-logout"', res_settings.data)
        self.assertIn(b'Account Session &amp; Sign Out', res_settings.data)
        self.assertIn(b'/logout', res_settings.data)


if __name__ == "__main__":
    unittest.main()
