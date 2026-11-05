import urllib.request
import time

for i in range(1, 15):
    try:
        url = f"https://smart-attendance-u7e6.onrender.com/static/js/punch.js?_={int(time.time())}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            js = r.read().decode("utf-8")
            if "maximumAge: 30000" in js:
                print("DEPLOYED! Latest build with quick GPS and location fixes is LIVE on Render!")
                exit(0)
            else:
                print(f"Attempt {i} ({time.strftime('%H:%M:%S')}): Building on Render...")
    except Exception as e:
        print(f"Attempt {i}: {e}")
    time.sleep(8)
