import time
import requests
import re

s = requests.Session()
t0 = time.time()
r = s.get("https://smart-attendance-u7e6.onrender.com/login/staff")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text).group(1)
t1 = time.time()

r2 = s.post("https://smart-attendance-u7e6.onrender.com/login/staff", data={
    "emp_code": "HR001",
    "password": "damo12345",
    "login_type": "staff",
    "csrf_token": csrf
})
t2 = time.time()

print(f"1. GET /login/staff : {(t1 - t0)*1000:.1f} ms")
print(f"2. POST /login/staff + Redirect to {r2.url} : {(t2 - t1)*1000:.1f} ms (Final Status: {r2.status_code})")
print("Redirect Hops:", [(h.status_code, h.headers.get("Location")) for h in r2.history])
print("Contains Signing in button in HTML:", "Signing in..." in r.text)
print(f"Total Login Duration: {(t2 - t0)*1000:.1f} ms")
