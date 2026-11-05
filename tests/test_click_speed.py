import sys
sys.path.insert(0, '.')
import time
from app import create_app

app = create_app()
with app.test_client() as c:
    with c.session_transaction() as sess:
        sess['uid'] = 1
        sess['_fresh'] = True
    
    # Simulate clicking between different pages
    click_sequence = [
        "/hr/dashboard",
        "/hr/employees",
        "/hr/attendance",
        "/hr/dashboard",  # User clicks back to dashboard!
        "/hr/shifts",
        "/hr/employees",  # User clicks back to employees!
        "/hr/approvals",
        "/hr/attendance", # User clicks back to attendance!
    ]
    
    print("=== SIMULATING USER CLICKING BETWEEN PAGES ===")
    for path in click_sequence:
        t0 = time.time()
        resp = c.get(path)
        ms = (time.time() - t0) * 1000
        print(f"Click -> {path:20} : {ms:6.1f} ms (Status: {resp.status_code})")
