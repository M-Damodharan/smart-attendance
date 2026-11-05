import time
import requests

BASE_URL = "https://smart-attendance-u7e6.onrender.com"
session = requests.Session()

print("--- MEASURING LIVE RELOAD SPEEDS ON RENDER ---")
# 1. Login as HR
t0 = time.time()
r_login_page = session.get(f"{BASE_URL}/login/staff", timeout=15)
import re
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', r_login_page.text).group(1)
r_post = session.post(f"{BASE_URL}/login/staff", data={
    "emp_code": "HR001",
    "password": "password123",
    "login_type": "staff",
    "csrf_token": csrf
}, timeout=15)

if "login" in r_post.url:
    # Try 123456789
    r_login_page = session.get(f"{BASE_URL}/login/staff", timeout=15)
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', r_login_page.text).group(1)
    r_post = session.post(f"{BASE_URL}/login/staff", data={
        "emp_code": "HR001",
        "password": "123456789",
        "login_type": "staff",
        "csrf_token": csrf
    }, timeout=15)

print(f"Login URL: {r_post.url} (Status: {r_post.status_code})")


pages_to_test = [
    "/hr/dashboard",
    "/hr/attendance",
    "/hr/employees",
    "/hr/cloud-backup-sync",
]

for path in pages_to_test:
    url = f"{BASE_URL}{path}"
    # First Load
    t_start = time.time()
    resp1 = session.get(url, timeout=15)
    t_first = (time.time() - t_start) * 1000
    
    # Second Load (Reload / Refresh)
    t_start = time.time()
    resp2 = session.get(url, timeout=15)
    t_reload = (time.time() - t_start) * 1000
    
    print(f"{path:25} | First Load: {t_first:6.1f} ms | Reload: {t_reload:6.1f} ms | Status: {resp2.status_code} | Size: {len(resp2.content)} bytes")
