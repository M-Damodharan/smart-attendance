import sys
sys.path.insert(0, ".")
import time
from app import create_app
from app.db import get_db

app = create_app()
app.config["TESTING"] = False

with app.test_client() as c:
    # 1. Measure GET /login/staff
    t0 = time.time()
    r_get = c.get("/login/staff")
    t_get = (time.time() - t0) * 1000
    print(f"GET /login/staff: {t_get:.1f} ms")
    
    import re
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', r_get.data.decode()).group(1)
    
    # 2. Measure POST /login/staff
    t0 = time.time()
    r_post = c.post("/login/staff", data={
        "emp_code": "HR001",
        "password": "123456789",
        "login_type": "staff",
        "csrf_token": csrf
    })
    t_post = (time.time() - t0) * 1000
    print(f"POST /login/staff: {t_post:.1f} ms (Status: {r_post.status_code}, Location: {r_post.headers.get('Location')})")
    
    # 3. Measure redirected page GET /hr/dashboard
    t0 = time.time()
    r_dash = c.get("/hr/dashboard")
    t_dash = (time.time() - t0) * 1000
    print(f"GET /hr/dashboard (Redirect landing): {t_dash:.1f} ms (Status: {r_dash.status_code})")
    print(f"TOTAL LOGIN TIME TO DASHBOARD: {t_post + t_dash:.1f} ms")
