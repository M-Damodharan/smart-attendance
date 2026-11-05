import sys
import os
import urllib.request
import urllib.error
import http.cookiejar
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.turso_db import connect_turso
import config

print("=" * 60)
print("1. CHECKING TURSO CLOUD DATABASE (AWS Mumbai)")
print("=" * 60)
c = connect_turso(config.Config.TURSO_DATABASE_URL, config.Config.TURSO_AUTH_TOKEN)

print("\n--- Current Employees in Turso Cloud ---")
employees = c.execute("SELECT id, emp_code, name, department, designation, role, category_id, active FROM employees").fetchall()
for emp in employees:
    print(f"[{emp['emp_code']}] {emp['name']} | Dept: {emp['department']} | Role: {emp['role']} | Active: {emp['active']}")

print("\n--- Current System Settings in Turso ---")
for s in c.execute("SELECT key, value FROM system_settings").fetchall():
    print(f"  {s['key']}: {s['value']}")

print("\n--- Current Offices in Turso (Geofence 50m) ---")
for off in c.execute("SELECT id, name, department, lat, lng, radius_m FROM offices").fetchall():
    print(f"  [{off['id']}] {off['name']} | Dept: {off['department']} | Lat/Lng: ({off['lat']}, {off['lng']}) | Radius: {off['radius_m']}m")

print("\n--- Current Shifts in Turso ---")
for sh in c.execute("SELECT id, name, start_time, end_time, grace_min FROM shifts").fetchall():
    print(f"  [{sh['id']}] {sh['name']} | {sh['start_time']} - {sh['end_time']} (Grace: {sh['grace_min']}m)")

print("\n" + "=" * 60)
print("2. TESTING LIVE PRODUCTION RENDER ENDPOINTS")
print("=" * 60)
LIVE_BASE = "https://smart-attendance-u7e6.onrender.com"

endpoints_to_check = [
    ("/", "Landing Page"),
    ("/login", "General Login"),
    ("/login/staff", "Staff / HR Login"),
    ("/login/employee", "Employee Login"),
    ("/login/contract", "Contract Login"),
    ("/login/accounts", "Accounts Login"),
    ("/api/health", "API Health / Status")
]

for path, label in endpoints_to_check:
    url = f"{LIVE_BASE}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AttendanceBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"  [OK {resp.status}] {label}: {url}")
    except urllib.error.HTTPError as e:
        print(f"  [HTTP {e.code}] {label}: {url}")
    except Exception as e:
        print(f"  [ERR] {label}: {url} -> {e}")

print("\nVerification check complete.")
