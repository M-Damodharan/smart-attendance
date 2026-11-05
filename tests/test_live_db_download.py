import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import re
import json

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Fetch login page
html = opener.open(f"{BASE_URL}/login/staff").read().decode("utf-8")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)

# Login
pdata = urllib.parse.urlencode({
    "emp_code": "HR001",
    "password": "damo12345",
    "login_type": "staff",
    "csrf_token": csrf
}).encode()
opener.open(urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0"}))

print("\n--- 1. Testing GET /hr/database/download-backup ---")
try:
    req = urllib.request.Request(f"{BASE_URL}/hr/database/download-backup", headers={"User-Agent": "Mozilla/5.0"})
    res = opener.open(req)
    print("Status:", res.status)
    print("Content-Type:", res.headers.get("Content-Type"))
    print("Content-Disposition:", res.headers.get("Content-Disposition"))
    data = res.read()
    print("Downloaded bytes:", len(data))
except urllib.error.HTTPError as e:
    print("HTTPError /hr/database/download-backup:", e.code, e.reason)
    print(e.read().decode("utf-8", errors="ignore")[:600])

print("\n--- 2. Testing POST /hr/cloud-backup-sync/api/backup ---")
try:
    headers = {"User-Agent": "Mozilla/5.0", "X-CSRFToken": csrf, "Content-Type": "application/json"}
    req = urllib.request.Request(f"{BASE_URL}/hr/cloud-backup-sync/api/backup", data=b"{}", headers=headers)
    res = opener.open(req)
    print("Status:", res.status)
    body = res.read().decode("utf-8")
    print("Body:", body[:300])
    data = json.loads(body)
    new_bid = data.get("backup_id")
    print("Newly created backup ID:", new_bid)
    
    if new_bid:
        print(f"\n--- 3. Testing GET /hr/cloud-backup-sync/download/{new_bid} immediately after backup creation ---")
        try:
            req = urllib.request.Request(f"{BASE_URL}/hr/cloud-backup-sync/download/{new_bid}", headers={"User-Agent": "Mozilla/5.0"})
            res = opener.open(req)
            print("Status:", res.status)
            print("Content-Type:", res.headers.get("Content-Type"))
            print("Content-Disposition:", res.headers.get("Content-Disposition"))
            dl_data = res.read()
            print("Downloaded bytes:", len(dl_data))
        except urllib.error.HTTPError as e:
            print("HTTPError downloading new backup:", e.code, e.reason)
            print(e.read().decode("utf-8", errors="ignore")[:600])
except urllib.error.HTTPError as e:
    print("HTTPError /hr/cloud-backup-sync/api/backup:", e.code, e.reason)
    print(e.read().decode("utf-8", errors="ignore")[:600])
except Exception as e:
    print("Exception /hr/cloud-backup-sync/api/backup:", e)
