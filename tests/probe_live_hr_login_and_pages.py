import urllib.request
import urllib.parse
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

print("Step 1: Fetching login page...")
req = urllib.request.Request(f"{BASE_URL}/login/staff", headers={"User-Agent": "Mozilla/5.0"})
resp = opener.open(req)
html = resp.read().decode("utf-8")
print(f"Login page status: {resp.status}")

# Check CSRF token if present
csrf_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
csrf_token = csrf_match.group(1) if csrf_match else None
print(f"CSRF Token found: {csrf_token}")

login_data = {
    "emp_code": "HR001",
    "password": "damo12345",
    "login_type": "staff"
}
if csrf_token:
    login_data["csrf_token"] = csrf_token

encoded_data = urllib.parse.urlencode(login_data).encode("utf-8")

print("\nStep 2: Submitting login credentials for HR001...")
login_req = urllib.request.Request(
    f"{BASE_URL}/login/staff",
    data=encoded_data,
    headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": f"{BASE_URL}/login/staff",
        "Content-Type": "application/x-www-form-urlencoded"
    }
)

login_resp = opener.open(login_req)
print(f"Redirected/Final URL: {login_resp.url}")
print(f"Status Code: {login_resp.status}")
resp_text = login_resp.read().decode("utf-8")

# Extract flash messages or alert divs
for line in resp_text.splitlines():
    if any(k in line.lower() for k in ["invalid", "incorrect", "alert", "danger", "error"]):
        print("MATCHED LINE:", line.strip())

print("\nCookies in jar:")
for c in cj:
    print(f"  {c.name}: {c.value} (domain={c.domain}, secure={c.secure})")
