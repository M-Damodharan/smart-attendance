import os
import json
import zipfile
import io
from app import create_app
from app.db import get_db

app = create_app()
client = app.test_client()

with client.session_transaction() as sess:
    sess["uid"] = 1

print("=" * 60)
print("TESTING DATABASE DOWNLOAD FIXES")
print("=" * 60)

# 1. Test /hr/database/download-backup (.ZIP)
print("\n[TEST 1] GET /hr/database/download-backup (.ZIP)...")
res1 = client.get("/hr/database/download-backup")
print(f"Status: {res1.status_code}")
print(f"Content-Type: {res1.content_type}")
print(f"Headers: {dict(res1.headers)}")
assert res1.status_code == 200, f"Expected 200, got {res1.status_code}"
assert res1.content_type == "application/zip", f"Expected application/zip, got {res1.content_type}"
# Verify it's a valid ZIP
zf = zipfile.ZipFile(io.BytesIO(res1.data))
namelist = zf.namelist()
print(f"ZIP contents: {namelist[:5]} (total {len(namelist)} items)")
assert "attendance.db" in namelist, "attendance.db missing from ZIP!"
print(">>> TEST 1 PASSED: Valid ZIP backup downloaded successfully!")

# 2. Test /hr/database/download-db (.DB)
print("\n[TEST 2] GET /hr/database/download-db (.DB)...")
res2 = client.get("/hr/database/download-db")
print(f"Status: {res2.status_code}")
print(f"Content-Type: {res2.content_type}")
print(f"Content-Length: {len(res2.data)} bytes")
assert res2.status_code == 200, f"Expected 200, got {res2.status_code}"
assert len(res2.data) > 0, "Downloaded .db is empty!"
# Check SQLite header
assert res2.data[:16] == b"SQLite format 3\x00", "File does not start with SQLite magic header!"
print(">>> TEST 2 PASSED: Valid SQLite attendance.db downloaded successfully!")

# 3. Test /hr/cloud-backup-sync/download/<backup_id> with missing local file (reconstruction)
print("\n[TEST 3] GET /hr/cloud-backup-sync/download/<backup_id> (Reconstruction on missing file)...")
with app.app_context():
    db = get_db()
    # Find a backup_id from cloud_backup_history
    row = db.execute("SELECT backup_id FROM cloud_backup_history ORDER BY created_at DESC LIMIT 1").fetchone()
    if row:
        bid = row["backup_id"]
        # Delete local snapshot file if it exists to test dynamic reconstruction
        base = app.config.get("BASE_DIR") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        local_fp = os.path.join(base, "backups", "cloud_snapshots", f"{bid}.json")
        if os.path.exists(local_fp):
            os.remove(local_fp)
            print(f"Deleted {local_fp} to force dynamic reconstruction testing.")

        res3 = client.get(f"/hr/cloud-backup-sync/download/{bid}")
        print(f"Status: {res3.status_code}")
        print(f"Content-Type: {res3.content_type}")
        print(f"Content-Length: {len(res3.data)} bytes")
        assert res3.status_code == 200, f"Expected 200, got {res3.status_code}"
        assert res3.content_type == "application/json", f"Expected application/json, got {res3.content_type}"
        data = json.loads(res3.data.decode("utf-8"))
        print(f"Reconstructed snapshot tables: {list(data.get('tables', {}).keys())}")
        print(f"Total records in snapshot: {data.get('total_records')}")
        assert data.get("total_records", 0) > 0, "Snapshot contains no records!"
        print(">>> TEST 3 PASSED: Dynamic reconstruction and download succeeded without 404/500!")

# 4. Test /hr/cloud-backup-sync/api/verify/<backup_id>
print("\n[TEST 4] GET /hr/cloud-backup-sync/api/verify/<backup_id>...")
if row:
    res4 = client.get(f"/hr/cloud-backup-sync/api/verify/{bid}")
    print(f"Status: {res4.status_code}")
    v_data = json.loads(res4.data.decode("utf-8"))
    print(f"Verify response: {v_data}")
    assert v_data.get("ok") is True, f"Verify failed: {v_data}"
    print(">>> TEST 4 PASSED: Backup verification works seamlessly!")

print("\n" + "=" * 60)
print("ALL DATABASE DOWNLOAD AND BACKUP TESTS PASSED!")
print("=" * 60)
