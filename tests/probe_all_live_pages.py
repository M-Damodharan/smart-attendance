import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# 1. Login as HR
print("Logging in as HR001...")
html = opener.open(f"{BASE_URL}/login/staff").read().decode("utf-8")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)
pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": "123456789", "login_type": "staff", "csrf_token": csrf}).encode()
opener.open(urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/staff"}))

# All GET routes to test
get_routes = [
    "/",
    "/dashboard",
    "/punch",
    "/leave",
    "/od",
    "/permission",
    "/face-change",
    "/history",
    "/switchable-accounts",
    "/healthz",
    "/hr/",
    "/hr/dashboard",
    "/hr/employees",
    "/hr/employees/new",
    "/hr/attendance",
    "/hr/shifts",
    "/hr/approvals",
    "/hr/corrections",
    "/hr/reports",
    "/hr/audit",
    "/hr/settings",
    "/hr/face-security-audit",
    "/hr/database/download-backup",
]

print("\n--- Testing all GET endpoints with active session ---")
errors_found = []

for r in get_routes:
    url = f"{BASE_URL}{r}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = opener.open(req)
        print(f"[{resp.status}] {r:30} -> {resp.url}")
    except urllib.error.HTTPError as e:
        print(f"[{e.code} ERROR] {r:30} -> {e}")
        errors_found.append((r, e.code))
        # Print error details
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
            print("   Message:", err_body[:250].replace("\n", " "))
        except Exception:
            pass
    except Exception as e:
        print(f"[EXCEPTION] {r:30} -> {e}")
        errors_found.append((r, str(e)))

print("\n" + "=" * 60)
if errors_found:
    print(f"SUMMARY: Found {len(errors_found)} error(s):")
    for r, code in errors_found:
        print(f"  - {r}: {code}")
else:
    print("ALL TESTED ENDPOINTS RETURNED HEALTHY RESPONSES (NO 500 ERRORS)!")
print("=" * 60)
