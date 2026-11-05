import urllib.request
import urllib.parse
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

test_credentials = [
    ("HR001", "damo12345", "staff"),
    ("HR001", "123456789", "staff"),
    ("HR001", "admin123", "staff"),
    ("HR001", "admin", "staff"),
    ("SBG001", "damo12345", "staff"),
    ("SBG001", "123456789", "staff"),
    ("EMP001", "123456789", "employee"),
    ("E0012", "123456789", "staff"),
]

for code, pw, ltype in test_credentials:
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(f"{BASE_URL}/login/{ltype}", headers={"User-Agent": "Mozilla/5.0"})
    html = opener.open(req).read().decode("utf-8")
    csrf_m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    csrf = csrf_m.group(1) if csrf_m else ""
    
    pdata = urllib.parse.urlencode({"emp_code": code, "password": pw, "login_type": ltype, "csrf_token": csrf}).encode()
    lreq = urllib.request.Request(f"{BASE_URL}/login/{ltype}", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/{ltype}"})
    try:
        lresp = opener.open(lreq)
        body = lresp.read().decode("utf-8")
        if "Invalid" not in body and "Access Denied" not in body:
            print(f"[SUCCESS] {code} with {pw} -> {lresp.url}")
        else:
            err = [l.strip() for l in body.splitlines() if "flash err" in l or "Invalid" in l]
            print(f"[FAILED] {code} with {pw} -> {err[:1]}")
    except Exception as e:
        print(f"[ERROR] {code} -> {e}")
