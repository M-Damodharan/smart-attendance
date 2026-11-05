import urllib.request
import urllib.parse
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Fetch login page
html = opener.open(f"{BASE_URL}/login/staff").read().decode("utf-8")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)

# Try damo12345 first, then 123456789
for pw in ["damo12345", "123456789"]:
    pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": pw, "login_type": "staff", "csrf_token": csrf}).encode()
    lresp = opener.open(urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/staff"}))
    body = lresp.read().decode("utf-8")
    if "/hr" in lresp.url or "Invalid employee code" not in body:
        print(f"Logged in successfully using password: {pw}")
        break
    else:
        print(f"Password {pw} did not work.")

# Now check /hr/dashboard
dash_resp = opener.open(f"{BASE_URL}/hr/dashboard")
dash_html = dash_resp.read().decode("utf-8")

print(f"\nDashboard URL: {dash_resp.url}")
print(f"Dashboard status: {dash_resp.status}")

# Check employee count or text
print("\nKey dashboard content:")
for line in dash_html.splitlines():
    if any(k in line.lower() for k in ["total employees", "present today", "on leave", "active staff", "sbg001", "damodharan"]):
        print("  ", line.strip())

# Check /hr/employees
emp_resp = opener.open(f"{BASE_URL}/hr/employees")
emp_html = emp_resp.read().decode("utf-8")
print("\nEmployee table lines:")
for line in emp_html.splitlines():
    if "emp_code" in line or "HR001" in line or "SBG001" in line or "EMP001" in line or "E0012" in line or "Ramesh" in line or "Damodharan" in line:
        print("  ", line.strip())
