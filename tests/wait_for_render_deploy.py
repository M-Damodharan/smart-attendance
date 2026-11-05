import urllib.request
import time
import re

print("Monitoring live Render deployment for commit e4dd0ca...")
for i in range(1, 40):
    try:
        req = urllib.request.Request("https://smart-attendance-u7e6.onrender.com/login/staff", headers={"User-Agent": "DeployCheck/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")
            if "Signing in..." in html:
                print(f"Render deployment LIVE at attempt {i} ({time.strftime('%H:%M:%S')})! Commit e4dd0ca is active.")
                break
            else:
                print(f"Attempt {i} ({time.strftime('%H:%M:%S')}): Render building/deploying new container...")
    except Exception as e:
        print(f"Attempt {i} ({time.strftime('%H:%M:%S')}): Container swapping/restarting ({e})...")
    time.sleep(10)
