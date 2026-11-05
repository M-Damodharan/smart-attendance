import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Check employees in Turso vs live
from app.turso_db import connect_turso
import config
c = connect_turso(config.Config.TURSO_DATABASE_URL, config.Config.TURSO_AUTH_TOKEN)
print("Employees in Turso:")
for e in c.execute("SELECT id, emp_code, name, department, role, active FROM employees").fetchall():
    print(f"  [{e['emp_code']}] {e['name']} ({e['role']})")

test_logins = [
    ("EMP001", "123456789", "employee"),
    ("SBG001", "damo12345", "staff"),
    ("E0012", "123456789", "staff"),
    ("CL001", "123456789", "contract"),
    ("S197", "123456789", "accounts"),
]

for code, pw, ltype in test_logins:
    print(f"\n--- Testing login for {code} ({ltype}) ---")
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        html = opener.open(f"{BASE_URL}/login/{ltype}").read().decode("utf-8")
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)
        pdata = urllib.parse.urlencode({"emp_code": code, "password": pw, "login_type": ltype, "csrf_token": csrf}).encode()
        resp = opener.open(urllib.request.Request(f"{BASE_URL}/login/{ltype}", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/{ltype}"}))
        body = resp.read().decode("utf-8")
        if "/login" not in resp.url or "dashboard" in resp.url.lower():
            print(f"[SUCCESS] {code} logged in -> {resp.url}")
            
            # Now test employee pages
            for ep in ["/dashboard", "/punch", "/leave", "/od", "/permission", "/history"]:
                try:
                    p_resp = opener.open(f"{BASE_URL}{ep}")
                    print(f"  [{p_resp.status}] {ep} -> {p_resp.url}")
                except urllib.error.HTTPError as he:
                    print(f"  [{he.code} ERROR] {ep}")
                    err_b = he.read().decode("utf-8", errors="ignore")
                    print("     Details:", err_b[:300].replace("\n", " "))
        else:
            flashes = re.findall(r'<div[^>]*class="[^"]*flash[^"]*"[^>]*>(.*?)</div>', body, re.DOTALL)
            clean_f = [re.sub(r'<[^>]+>', '', f).strip() for f in flashes]
            print(f"[LOGIN FAILED] {code}: {clean_f}")
    except Exception as ex:
        print(f"[EXCEPTION] {code}: {ex}")
