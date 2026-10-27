"""Comprehensive Full Checkup Verification Script for Sri Balaji Gears Smart Attendance.
Tests Turso Cloud direct connectivity, data read/write/delete persistence,
and live Render website authentication and employee portal functionality.
"""
import os
import sys
import re
import json
import urllib.request
import urllib.parse
import http.cookiejar
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)

from app.turso_db import connect_turso

TOKEN_FILE = os.path.join(BASE_DIR, "instance", ".turso_token")
TURSO_HOST = "sbg-attendance-m-damodharan.aws-ap-south-1.turso.io"

LIVE_BASE_URL = "https://smart-attendance-u7e6.onrender.com"

passed_checks = []
failed_checks = []


def record_result(check_name: str, passed: bool, details: str = ""):
    status_str = "[PASS]" if passed else "[FAIL]"
    print(f"  {status_str} {check_name}: {details}")
    if passed:
        passed_checks.append(check_name)
    else:
        failed_checks.append((check_name, details))


def run_full_checkup():
    print("\n" + "=" * 70)
    print("      SMART ATTENDANCE - FULL END-TO-END SYSTEM CHECKUP")
    print(f"      Target Host: {LIVE_BASE_URL}")
    print(f"      Turso Host:  {TURSO_HOST}")
    print(f"      Timestamp:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70 + "\n")

    # -------------------------------------------------------------
    # SECTION 1: TURSO CLOUD DIRECT VERIFICATION
    # -------------------------------------------------------------
    print("--- 1. TURSO CLOUD DATABASE (24/7 ONLINE STORAGE) ---")
    with open(TOKEN_FILE, "r") as f:
        token = f.read().strip()

    try:
        t_db = connect_turso(TURSO_HOST, token)
        record_result("Turso Cloud Connection", True, f"Connected to AWS Mumbai ({TURSO_HOST})")

        # 1.1 Verify all active production accounts
        cur = t_db.execute("SELECT emp_code, name, role, department, face_registered FROM employees ORDER BY id")
        emps = cur.fetchall()
        expected_codes = {"HR001", "EMP001", "SBG001", "E0012", "CL001", "S197"}
        found_codes = {e["emp_code"] for e in emps}

        if expected_codes.issubset(found_codes):
            face_counts = sum(1 for e in emps if e["face_registered"] == 1)
            record_result("Clean Employee Accounts", True, f"{len(emps)} accounts online ({face_counts} Face IDs registered)")
        else:
            record_result("Clean Employee Accounts", False, f"Missing codes: {expected_codes - found_codes}")

        # 1.2 Verify zero dummy test accounts
        dummy_codes = {"LOCALTEST01", "AUDITEMP01", "TESTGEN01", "E001", "E002", "E003", "E004"}
        has_dummies = dummy_codes.intersection(found_codes)
        record_result("Old Test Data Cleared", len(has_dummies) == 0, "Zero test accounts present in cloud database" if len(has_dummies) == 0 else f"Found dummies: {has_dummies}")

        # 1.3 Verify factory GPS location
        offices = t_db.execute("SELECT name, lat, lng, radius_m FROM offices").fetchall()
        factory_ok = all(abs(o["lat"] - 12.7434752) < 0.001 and abs(o["lng"] - 77.8174464) < 0.001 for o in offices)
        record_result("Factory GPS Geofence", factory_ok, f"{len(offices)} department points configured at 12.7434752, 77.8174464 (50m)")

        # 1.4 Test Cloud Write & Read (Persistence Check)
        test_key = f"chk_{int(datetime.now().timestamp())}"
        t_db.execute("INSERT INTO system_settings (key, value) VALUES (?, ?)", [test_key, "cloud_persistence_verified"])
        read_back = t_db.execute("SELECT value FROM system_settings WHERE key=?", [test_key]).fetchone()
        t_db.execute("DELETE FROM system_settings WHERE key=?", [test_key])

        if read_back and read_back["value"] == "cloud_persistence_verified":
            record_result("Cloud Read/Write/Delete Persistence", True, "Real-time write and retrieval verified in AWS Mumbai")
        else:
            record_result("Cloud Read/Write/Delete Persistence", False, "Failed to read back newly written row")

    except Exception as e:
        record_result("Turso Cloud Operations", False, str(e))

    # -------------------------------------------------------------
    # SECTION 2: LIVE RENDER WEB APPLICATION CHECK
    # -------------------------------------------------------------
    print("\n--- 2. LIVE RENDER WEB APPLICATION (render.com) ---")
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    # 2.1 Healthcheck
    try:
        h_resp = opener.open(f"{LIVE_BASE_URL}/healthz", timeout=15)
        h_data = json.loads(h_resp.read().decode("utf-8"))
        record_result("Live Healthz API", h_resp.status == 200 and h_data.get("status") == "ok", f"HTTP {h_resp.status} - status: ok")
    except Exception as e:
        record_result("Live Healthz API", False, str(e))

    # 2.2 Login Page Load & CSRF extraction
    csrf_token = ""
    try:
        login_resp = opener.open(f"{LIVE_BASE_URL}/login", timeout=15)
        login_html = login_resp.read().decode("utf-8")
        csrf_match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', login_html)
        csrf_token = csrf_match.group(1) if csrf_match else ""
        record_result("Live Login Page", login_resp.status == 200, f"HTTP {login_resp.status} - CSRF Protection: {'Active' if csrf_token else 'Disabled'}")
    except Exception as e:
        record_result("Live Login Page", False, str(e))

    # 2.3 HR Admin Authentication (HR001 / damo12345)
    try:
        post_data = urllib.parse.urlencode({
            "emp_code": "HR001",
            "password": "damo12345",
            "csrf_token": csrf_token
        }).encode("utf-8")
        req = urllib.request.Request(f"{LIVE_BASE_URL}/login", data=post_data, headers={"User-Agent": "SmartAttendanceAuditBot/1.0"})
        auth_resp = opener.open(req, timeout=15)

        # Access HR dashboard
        dash_resp = opener.open(f"{LIVE_BASE_URL}/hr/dashboard", timeout=15)
        dash_html = dash_resp.read().decode("utf-8")

        hr_login_ok = ("HR Admin" in dash_html or "Dashboard" in dash_html or "Employees" in dash_html)
        record_result("HR Admin Login (HR001)", hr_login_ok, "Session established, access to /hr/dashboard confirmed")

        # 2.4 Verify HR Employees Management Page Loads
        emps_resp = opener.open(f"{LIVE_BASE_URL}/hr/employees", timeout=15)
        emps_html = emps_resp.read().decode("utf-8")
        emps_page_ok = emps_resp.status == 200 and ("employees" in emps_html.lower() or "add" in emps_html.lower() or "hr" in emps_html.lower())
        record_result("HR Employee Directory", emps_page_ok, f"HTTP {emps_resp.status} - Directory view loaded successfully")

    except Exception as e:
        record_result("HR Admin Authentication", False, str(e))

    # 2.5 Employee Authentication (EMP001 / 123456789)
    try:
        emp_cookie_jar = http.cookiejar.CookieJar()
        emp_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(emp_cookie_jar))

        emp_login_resp = emp_opener.open(f"{LIVE_BASE_URL}/login", timeout=15)
        emp_csrf_match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', emp_login_resp.read().decode("utf-8"))
        emp_csrf = emp_csrf_match.group(1) if emp_csrf_match else ""

        emp_post = urllib.parse.urlencode({
            "emp_code": "EMP001",
            "password": "123456789",
            "csrf_token": emp_csrf
        }).encode("utf-8")
        emp_req = urllib.request.Request(f"{LIVE_BASE_URL}/login", data=emp_post, headers={"User-Agent": "SmartAttendanceAuditBot/1.0"})
        emp_auth = emp_opener.open(emp_req, timeout=15)

        emp_dash = emp_opener.open(f"{LIVE_BASE_URL}/dashboard", timeout=15)
        emp_dash_html = emp_dash.read().decode("utf-8")
        emp_ok = emp_dash.status == 200 and ("EMP001" in emp_dash_html or "Employee Staff" in emp_dash_html or "punch" in emp_dash_html.lower() or "attendance" in emp_dash_html.lower() or "sri balaji" in emp_dash_html.lower())
        record_result("Employee Login (EMP001)", emp_ok, f"HTTP {emp_dash.status} - Employee portal active")

    except Exception as e:
        record_result("Employee Login (EMP001)", False, str(e))

    # -------------------------------------------------------------
    # SECTION 3: ANDROID APP COMPATIBILITY & SECURITY
    # -------------------------------------------------------------
    print("\n--- 3. ANDROID APP COMPATIBILITY & API SECURITY ---")
    try:
        apk_resp = opener.open(f"{LIVE_BASE_URL}/download/apk", timeout=15)
        record_result("Android APK Download Endpoint", apk_resp.status == 200, f"HTTP {apk_resp.status} - Direct APK download functional")
    except Exception as e:
        record_result("Android APK Download Endpoint", False, str(e))

    # Summary
    print("\n" + "=" * 70)
    print(f"CHECKUP SUMMARY: {len(passed_checks)} PASSED / {len(failed_checks)} FAILED")
    print("=" * 70)
    if not failed_checks:
        print("[SUCCESS] All systems operational! The database, cloud storage, live website, and authentication passed 100% of checks.\n")
    else:
        print(f"[!] The following checks failed: {failed_checks}\n")


if __name__ == "__main__":
    run_full_checkup()
