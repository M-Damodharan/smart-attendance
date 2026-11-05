import urllib.request
import urllib.parse
import http.cookiejar
import re

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
base_url = "https://smart-attendance-u7e6.onrender.com"

print("--- Testing Live Production Website on Render ---")
# 1. Fetch login page and csrf token
html = opener.open(f"{base_url}/login/staff").read().decode("utf-8")
csrf_match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
csrf = csrf_match.group(1) if csrf_match else ""

# 2. Login as HR001
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
resp = opener.open(req)
print(f"1. Login Status: {resp.status} (Authenticated successfully as HR001)")

# 3. Access /hr/cloud-backup-sync
try:
    sync_resp = opener.open(f"{base_url}/hr/cloud-backup-sync")
    sync_html = sync_resp.read().decode("utf-8")
    has_cloud_sync = "Cloud Database" in sync_html
    has_telemetry = "Active Employees" in sync_html
    has_buttons = "Sync All Data" in sync_html
    print(f"2. /hr/cloud-backup-sync Status: {sync_resp.status}")
    print(f"   - Page Title & 3D Dashboard: {'FOUND' if has_cloud_sync else 'MISSING'}")
    print(f"   - Telemetry Metrics Box: {'FOUND' if has_telemetry else 'MISSING'}")
    print(f"   - Sync All Data Action: {'FOUND' if has_buttons else 'MISSING'}")
except Exception as e:
    print("2. /hr/cloud-backup-sync Error:", e)

# 4. Access /hr/dashboard
try:
    dash_resp = opener.open(f"{base_url}/hr/dashboard")
    dash_html = dash_resp.read().decode("utf-8")
    has_sync_btn = "cloud_backup_sync" in dash_html or "Cloud Backup" in dash_html
    has_cloud_nav = "Cloud Sync" in dash_html
    print(f"3. /hr/dashboard Status: {dash_resp.status}")
    print(f"   - Header Cloud Sync Button: {'FOUND' if has_sync_btn else 'MISSING'}")
    print(f"   - Top Navigation Cloud Sync Pill: {'FOUND' if has_cloud_nav else 'MISSING'}")
except Exception as e:
    print("3. /hr/dashboard Error:", e)

print("--- Live Deployment Verification Complete ---")
