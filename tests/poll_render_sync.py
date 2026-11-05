import time
import urllib.request
import urllib.parse
import http.cookiejar
import re

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
base_url = "https://smart-attendance-u7e6.onrender.com"

print("Waiting for Render deployment to complete...", flush=True)

for attempt in range(1, 20):
    try:
        # Re-login to ensure fresh session
        html = opener.open(f"{base_url}/login/staff", timeout=12).read().decode("utf-8")
        csrf_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        csrf = csrf_match.group(1) if csrf_match else ""
        data = urllib.parse.urlencode({
            "emp_code": "HR001",
            "password": "damo12345",
            "login_type": "staff",
            "csrf_token": csrf
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/login/staff",
            data=data,
            headers={"User-Agent": "Mozilla/5.0", "Referer": f"{base_url}/login/staff"}
        )
        opener.open(req, timeout=12)

        # Check /hr/cloud-backup-sync
        sync_resp = opener.open(f"{base_url}/hr/cloud-backup-sync", timeout=12)
        if sync_resp.status == 200:
            print(f"[Attempt {attempt}] SUCCESS! Render deployment is LIVE!", flush=True)
            print(f"URL: {base_url}/hr/cloud-backup-sync returned HTTP 200 OK.", flush=True)
            break
    except urllib.error.HTTPError as e:
        print(f"[Attempt {attempt}] HTTP {e.code}: Deployment in progress on Render, retrying in 15s...", flush=True)
    except Exception as e:
        print(f"[Attempt {attempt}] Waiting for server ({e}), retrying in 15s...", flush=True)
    time.sleep(15)
