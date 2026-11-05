import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Login as HR
print("Logging in as HR001...")
html = opener.open(f"{BASE_URL}/login/staff").read().decode("utf-8")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)
pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": "123456789", "login_type": "staff", "csrf_token": csrf}).encode()
opener.open(urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/staff"}))

# Test POST actions
post_actions = [
    ("/hr/face-security-audit/scan", {}),
    ("/hr/maintenance/clear-cache", {}),
    ("/hr/database/create-snapshot", {}),
    ("/hr/corrections", {"emp_code": "SBG001", "date": "2026-10-27", "action": "in", "time": "09:00", "status": "P"}),
    ("/api/geo-check", {"lat": 12.7434752, "lng": 77.8174464}),
]

print("\n--- Testing POST endpoints for HTTP 500 ---")
for path, payload in post_actions:
    payload["csrf_token"] = csrf
    encoded = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=encoded,
        headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/hr/dashboard"}
    )
    try:
        resp = opener.open(req)
        print(f"[{resp.status}] POST {path} -> Final: {resp.url}")
    except urllib.error.HTTPError as e:
        print(f"[{e.code} ERROR] POST {path}")
        body = e.read().decode("utf-8", errors="ignore")
        print("   Body:", body[:300].replace("\n", " "))
    except Exception as e:
        print(f"[EXCEPTION] POST {path} -> {e}")
