import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

print("=" * 60)
print("PROBING ALL PUBLIC AND AUTHENTICATED PAGES FOR HTTP 500 ERRORS")
print("=" * 60)

public_urls = [
    "/",
    "/login",
    "/login/staff",
    "/login/employee",
    "/login/contract",
    "/login/accounts",
    "/static/css/style.css",
]

for p in public_urls:
    url = f"{BASE_URL}{p}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[{resp.status}] {p}")
    except urllib.error.HTTPError as e:
        print(f"[{e.code} ERROR] {p}")
        body = e.read().decode("utf-8", errors="ignore")
        print("  Snippet:", body[:200].replace("\n", " "))
    except Exception as e:
        print(f"[EXCEPTION] {p} -> {e}")

print("\n--- Now testing login and authenticated HR pages ---")
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Fetch login page
try:
    l_req = urllib.request.Request(f"{BASE_URL}/login/staff", headers={"User-Agent": "Mozilla/5.0"})
    l_resp = opener.open(l_req)
    html = l_resp.read().decode("utf-8")
    csrf_m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    csrf = csrf_m.group(1) if csrf_m else ""

    # Login as HR001 with damo12345 or 123456789
    for pw in ["damo12345", "123456789"]:
        pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": pw, "login_type": "staff", "csrf_token": csrf}).encode()
        try:
            auth_resp = opener.open(urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/staff"}))
            body = auth_resp.read().decode("utf-8")
            if "/hr" in auth_resp.url or "Invalid employee code" not in body:
                print(f"[LOGIN SUCCESS] Logged in with password: {pw}")
                break
        except urllib.error.HTTPError as e:
            print(f"[LOGIN ERROR with {pw}] HTTP {e.code}")

    hr_endpoints = [
        "/hr/dashboard",
        "/hr/employees",
        "/hr/employee/new",
        "/hr/attendance",
        "/hr/shifts",
        "/hr/approvals",
        "/hr/corrections",
        "/hr/reports",
        "/hr/audit",
        "/hr/settings",
        "/hr/face-security-audit",
    ]

    for p in hr_endpoints:
        url = f"{BASE_URL}{p}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = opener.open(req)
            print(f"[{resp.status}] {p} -> Final: {resp.url}")
        except urllib.error.HTTPError as e:
            print(f"[{e.code} ERROR] {p}")
            err_body = e.read().decode("utf-8", errors="ignore")
            print("  Error snippet:", err_body[:300].replace("\n", " "))
        except Exception as e:
            print(f"[EXCEPTION] {p} -> {e}")

except Exception as e:
    print(f"[FAILED TO CONNECT] {e}")
