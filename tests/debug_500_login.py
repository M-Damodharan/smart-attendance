import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# 1. Fetch /login/staff
req = urllib.request.Request(f"{BASE_URL}/login/staff", headers={"User-Agent": "Mozilla/5.0"})
html = opener.open(req).read().decode("utf-8")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)

print(f"CSRF: {csrf}")

# 2. Try POST with HR001 / damo12345
pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": "damo12345", "login_type": "staff", "csrf_token": csrf}).encode()
post_req = urllib.request.Request(
    f"{BASE_URL}/login/staff",
    data=pdata,
    headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": f"{BASE_URL}/login/staff",
        "Content-Type": "application/x-www-form-urlencoded"
    }
)

try:
    resp = opener.open(post_req, timeout=30)
    print("Success status:", resp.status)
    print("Final URL:", resp.url)
    print("Body preview:", resp.read().decode("utf-8")[:300])
except urllib.error.HTTPError as e:
    print(f"HTTP ERROR: {e.code}")
    print("Headers:", dict(e.headers))
    print("Body:", e.read().decode("utf-8", errors="ignore")[:500])
except Exception as e:
    print(f"Exception: {e}")
