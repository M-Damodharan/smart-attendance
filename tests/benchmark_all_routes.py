import sys
sys.path.insert(0, ".")
import time
from app import create_app
from app.db import get_db

app = create_app()

with app.test_client() as c:
    with c.session_transaction() as sess:
        sess["uid"] = 1  # HR001
        sess["_fresh"] = True
    
    routes = [
        "/hr/dashboard",
        "/hr/employees",
        "/hr/employees/new",
        "/hr/attendance",
        "/hr/shifts",
        "/hr/approvals",
        "/hr/corrections",
        "/hr/reports",
        "/hr/audit",
        "/hr/settings",
        "/hr/face-security-audit",
        "/hr/cloud-backup-sync",
    ]
    
    print("=== HR ROUTES EXECUTION PROFILE ===")
    for r in routes:
        t0 = time.time()
        resp = c.get(r)
        elapsed = (time.time() - t0) * 1000
        print(f"{r:25} -> {elapsed:6.1f} ms (Status: {resp.status_code}, Length: {len(resp.data)} bytes)")

with app.test_client() as c:
    with c.session_transaction() as sess:
        sess["uid"] = 20  # SBG001
        sess["_fresh"] = True
    
    emp_routes = [
        "/dashboard",
        "/punch",
        "/leave",
        "/od",
        "/permission",
        "/history",
        "/face-change",
    ]
    
    print("\n=== EMPLOYEE ROUTES EXECUTION PROFILE ===")
    for r in emp_routes:
        t0 = time.time()
        resp = c.get(r)
        elapsed = (time.time() - t0) * 1000
        print(f"{r:25} -> {elapsed:6.1f} ms (Status: {resp.status_code}, Length: {len(resp.data)} bytes)")
