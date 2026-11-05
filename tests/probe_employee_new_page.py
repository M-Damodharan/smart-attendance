import urllib.request
import urllib.parse
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Login
html = opener.open(f"{BASE_URL}/login/staff").read().decode("utf-8")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)
pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": "123456789", "login_type": "staff", "csrf_token": csrf}).encode()
opener.open(urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/staff"}))

# Try GET /hr/employees/new
try:
    resp = opener.open(f"{BASE_URL}/hr/employees/new")
    print(f"GET /hr/employees/new status: {resp.status}")
except urllib.error.HTTPError as e:
    print(f"GET /hr/employees/new HTTP ERROR: {e.code}")
    print(e.read().decode("utf-8", errors="ignore")[:500])

# Try POST /hr/employees/new with SBG001
new_emp_data = {
    "emp_code": "SBG001",
    "name": "Damodharan",
    "department": "IT",
    "designation": "Software Engineer",
    "role": "employee",
    "password": "damo12345",
    "csrf_token": csrf,
    "active": "1"
}
try:
    post_req = urllib.request.Request(
        f"{BASE_URL}/hr/employees/new",
        data=urllib.parse.urlencode(new_emp_data).encode("utf-8"),
        headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/hr/employees/new"}
    )
    post_resp = opener.open(post_req)
    print(f"POST /hr/employees/new status: {post_resp.status}, URL: {post_resp.url}")
    p_body = post_resp.read().decode("utf-8", errors="ignore")
    flashes = re.findall(r'<div[^>]*class="[^"]*flash[^"]*"[^>]*>(.*?)</div>', p_body, re.DOTALL)
    for f in flashes:
        print("FLASH:", re.sub(r'<[^>]+>', '', f).strip())
except urllib.error.HTTPError as e:
    print(f"POST /hr/employees/new HTTP ERROR: {e.code}")
    print(e.read().decode("utf-8", errors="ignore")[:500])
