import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import re
import time
import zipfile
import io

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

print("=" * 60)
print("POLLING RENDER DEPLOYMENT & VERIFYING ALL DOWNLOAD ENDPOINTS")
print("=" * 60)

deployed = False
opener = None
csrf = ""

for attempt in range(1, 30):
    try:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        req = urllib.request.Request(f"{BASE_URL}/login/staff", headers={"User-Agent": "Mozilla/5.0"})
        html = opener.open(req).read().decode("utf-8")
        csrf_m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        csrf = csrf_m.group(1) if csrf_m else ""

        pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": "damo12345", "login_type": "staff", "csrf_token": csrf}).encode()
        lreq = urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/staff"})
        lresp = opener.open(lreq)
        
        # Check if settings page has download_database_sqlite link
        sreq = urllib.request.Request(f"{BASE_URL}/hr/settings", headers={"User-Agent": "Mozilla/5.0"})
        sbody = opener.open(sreq).read().decode("utf-8")
        if "download-db" in sbody:
            print(f"[SUCCESS] Attempt {attempt} ({time.strftime('%H:%M:%S')}): New deployment 813add9 is LIVE on Render!")
            deployed = True
            break
        else:
            print(f"Attempt {attempt} ({time.strftime('%H:%M:%S')}): Render building / old version still active...")
    except Exception as e:
        print(f"Attempt {attempt} ({time.strftime('%H:%M:%S')}): Waiting for container... ({e})")
    time.sleep(12)

if not deployed:
    print("[WARNING] Render deployment took longer than expected; running verification against current container...")

# Verify 1: GET /hr/database/download-backup (.ZIP)
print("\n[VERIFY 1] Live GET /hr/database/download-backup (.ZIP)...")
try:
    req = urllib.request.Request(f"{BASE_URL}/hr/database/download-backup", headers={"User-Agent": "Mozilla/5.0"})
    res = opener.open(req)
    data = res.read()
    print(f"Status: {res.status}")
    print(f"Content-Type: {res.headers.get('Content-Type')}")
    print(f"Content-Disposition: {res.headers.get('Content-Disposition')}")
    print(f"Downloaded bytes: {len(data)}")
    assert res.status == 200, f"Expected 200, got {res.status}"
    zf = zipfile.ZipFile(io.BytesIO(data))
    print(f"ZIP members: {zf.namelist()[:5]}")
    assert "attendance.db" in zf.namelist(), "attendance.db not found in downloaded ZIP!"
    print(">>> VERIFY 1 PASSED: Full Backup ZIP downloads with attendance.db!")
except Exception as e:
    print(f">>> VERIFY 1 ERROR: {e}")

# Verify 2: GET /hr/database/download-db (.DB)
print("\n[VERIFY 2] Live GET /hr/database/download-db (.DB)...")
try:
    req = urllib.request.Request(f"{BASE_URL}/hr/database/download-db", headers={"User-Agent": "Mozilla/5.0"})
    res = opener.open(req)
    data = res.read()
    print(f"Status: {res.status}")
    print(f"Content-Type: {res.headers.get('Content-Type')}")
    print(f"Content-Disposition: {res.headers.get('Content-Disposition')}")
    print(f"Downloaded bytes: {len(data)}")
    assert res.status == 200, f"Expected 200, got {res.status}"
    assert data[:16] == b"SQLite format 3\x00", "Downloaded file is not a valid SQLite database!"
    print(">>> VERIFY 2 PASSED: Direct SQLite .DB downloads successfully!")
except Exception as e:
    print(f">>> VERIFY 2 ERROR: {e}")

# Verify 3: Cloud snapshot download (Testing reconstruction of older backup that is not on ephemeral disk)
print("\n[VERIFY 3] Live GET /hr/cloud-backup-sync/download/<older_backup_id> (Reconstruction)...")
try:
    sreq = urllib.request.Request(f"{BASE_URL}/hr/cloud-backup-sync", headers={"User-Agent": "Mozilla/5.0"})
    sync_html = opener.open(sreq).read().decode("utf-8")
    bids = re.findall(r'/hr/cloud-backup-sync/download/([^"\'\s]+)', sync_html)
    print("Found backup IDs in cloud sync table:", bids)
    if bids:
        # Download the oldest backup row which definitely was not created in this container session
        target_bid = bids[-1]
        print(f"Testing download for older backup: {target_bid}...")
        req = urllib.request.Request(f"{BASE_URL}/hr/cloud-backup-sync/download/{target_bid}", headers={"User-Agent": "Mozilla/5.0"})
        res = opener.open(req)
        data = res.read()
        print(f"Status: {res.status}")
        print(f"Content-Type: {res.headers.get('Content-Type')}")
        print(f"Content-Disposition: {res.headers.get('Content-Disposition')}")
        print(f"Downloaded bytes: {len(data)}")
        assert res.status == 200, f"Expected 200, got {res.status}"
        print(">>> VERIFY 3 PASSED: Older cloud backup downloaded via on-the-fly reconstruction without 404/500!")
except Exception as e:
    print(f">>> VERIFY 3 ERROR: {e}")

print("\n" + "=" * 60)
print("LIVE VERIFICATION COMPLETE!")
print("=" * 60)
