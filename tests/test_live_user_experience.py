import time
import requests

BASE_URL = "https://smart-attendance-u7e6.onrender.com"
session = requests.Session()

print("=== MEASURING LIVE RENDER EXPERIENCE NOW ===")

# 1. Login
t0 = time.time()
r_login_page = session.get(f"{BASE_URL}/login/staff")
import re
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', r_login_page.text).group(1)

t_post0 = time.time()
r_post = session.post(f"{BASE_URL}/login/staff", data={
    "emp_code": "HR001",
    "password": "password123",
    "login_type": "staff",
    "csrf_token": csrf
})
if "login" in r_post.url:
    r_login_page = session.get(f"{BASE_URL}/login/staff")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', r_login_page.text).group(1)
    t_post0 = time.time()
    r_post = session.post(f"{BASE_URL}/login/staff", data={
        "emp_code": "HR001",
        "password": "123456789",
        "login_type": "staff",
        "csrf_token": csrf
    })
print(f"Live Login POST: {(time.time() - t_post0)*1000:.1f} ms -> {r_post.url}")

# 2. Simulate User Clicking Through Pages
nav_sequence = [
    "/hr/dashboard",
    "/hr/employees",
    "/hr/attendance",
    "/hr/shifts",
    "/hr/approvals",
    "/hr/dashboard",
    "/hr/employees",
    "/hr/attendance",
]

print("\n--- SIMULATING USER PAGE CLICKS ON LIVE RENDER ---")
for path in nav_sequence:
    t_click = time.time()
    resp = session.get(f"{BASE_URL}{path}", headers={"X-Requested-With": "InstantNav"})
    ms = (time.time() - t_click) * 1000
    print(f"Click -> {path:22} : {ms:6.1f} ms (Status: {resp.status_code}, Size: {len(resp.content)} bytes)")
