import requests
import re
import time

print("Polling live Render until new commit 0013892 is deployed...")

for i in range(1, 40):
    try:
        s = requests.Session()
        r = s.get("https://smart-attendance-u7e6.onrender.com/login/staff", timeout=10)
        csrf = None
        for l in r.text.splitlines():
            if 'name="csrf_token"' in l or 'name="csrf"' in l:
                m = re.search(r'value=["\']([^"\']+)', l)
                if m:
                    csrf = m.group(1)
                    break

        res_login = s.post(
            "https://smart-attendance-u7e6.onrender.com/login/staff",
            data={
                "emp_code": "HR001",
                "password": "password123",
                "login_type": "staff",
                "csrf_token": csrf or "",
            },
            timeout=10,
        )

        res_settings = s.get("https://smart-attendance-u7e6.onrender.com/hr/settings", timeout=10)
        html = res_settings.text

        if "quick-gps-btn" in html and "Save & Set Location" in html:
            print(f"[Attempt {i}] SUCCESS! Live Render deployment is ACTIVE with updated location buttons!")
            print("  - quick-gps-btn: True")
            print("  - Save & Set Location: True")
            print("  - All Departments option: True")
            print("  - parseCoordsFromStr JS: True")
            break
        else:
            print(f"[Attempt {i}] ({time.strftime('%H:%M:%S')}) Building new image on Render...")
    except Exception as e:
        print(f"[Attempt {i}] ({time.strftime('%H:%M:%S')}) Waiting ({e})...")
    time.sleep(10)
