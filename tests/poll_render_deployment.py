import urllib.request
import urllib.parse
import http.cookiejar
import re
import time

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

print("Waiting for Render to finish deploying latest commit e1032f2...")

for attempt in range(1, 25):
    try:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        req = urllib.request.Request(f"{BASE_URL}/login/staff", headers={"User-Agent": "Mozilla/5.0"})
        html = opener.open(req).read().decode("utf-8")
        csrf_m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        csrf = csrf_m.group(1) if csrf_m else ""
        
        # Test damo12345
        pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": "damo12345", "login_type": "staff", "csrf_token": csrf}).encode()
        lreq = urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/staff"})
        lresp = opener.open(lreq)
        body = lresp.read().decode("utf-8")
        
        if "/hr" in lresp.url or "Invalid employee code" not in body:
            print(f"[DEPLOYED!] Attempt {attempt}: HR001 logged in successfully with damo12345!")
            break
        else:
            print(f"Attempt {attempt} ({time.strftime('%H:%M:%S')}): Still on previous container (damo12345 not active yet)...")
    except Exception as e:
        print(f"Attempt {attempt}: Server restarting / error: {e}")
    time.sleep(10)
