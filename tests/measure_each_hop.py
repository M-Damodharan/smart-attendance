import time
import requests
import re

s = requests.Session()
t0 = time.time()
r_get = s.get("https://smart-attendance-u7e6.onrender.com/login/staff")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', r_get.text).group(1)
t1 = time.time()
print(f"Hop 1: GET /login/staff -> {(t1 - t0)*1000:.1f} ms (Status: {r_get.status_code})")

t0 = time.time()
r_post = s.post("https://smart-attendance-u7e6.onrender.com/login/staff", data={
    "emp_code": "HR001",
    "password": "damo12345",
    "login_type": "staff",
    "csrf_token": csrf
}, allow_redirects=False)
t1 = time.time()
loc1 = r_post.headers.get("Location")
print(f"Hop 2: POST /login/staff -> {(t1 - t0)*1000:.1f} ms (Status: {r_post.status_code}, Location: {loc1})")

if loc1:
    t0 = time.time()
    r_hop1 = s.get("https://smart-attendance-u7e6.onrender.com" + loc1, allow_redirects=False)
    t1 = time.time()
    loc2 = r_hop1.headers.get("Location")
    print(f"Hop 3: GET {loc1} -> {(t1 - t0)*1000:.1f} ms (Status: {r_hop1.status_code}, Location: {loc2})")
    
    if loc2:
        t0 = time.time()
        r_hop2 = s.get("https://smart-attendance-u7e6.onrender.com" + loc2, allow_redirects=False)
        t1 = time.time()
        print(f"Hop 4: GET {loc2} -> {(t1 - t0)*1000:.1f} ms (Status: {r_hop2.status_code})")

# Test subsequent page clicks in session
for next_page in ["/hr/dashboard", "/hr/attendance", "/hr/employees", "/hr/shifts"]:
    t0 = time.time()
    r = s.get("https://smart-attendance-u7e6.onrender.com" + next_page)
    ms = (time.time() - t0) * 1000
    print(f"Click -> {next_page:20} : {ms:6.1f} ms (Status: {r.status_code})")
