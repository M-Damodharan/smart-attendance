import urllib.request
import urllib.parse
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# 1. Login with HR001 / 123456789
html = opener.open(f"{BASE_URL}/login/staff").read().decode("utf-8")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)
pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": "123456789", "login_type": "staff", "csrf_token": csrf}).encode()
login_resp = opener.open(urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/staff"}))

print(f"Logged in, URL: {login_resp.url}")

# 2. Scrape HR Dashboard
dash_html = opener.open(f"{BASE_URL}/hr/dashboard").read().decode("utf-8")
print("\n--- HR Dashboard title & stats ---")
title_m = re.search(r'<title>(.*?)</title>', dash_html)
print("Title:", title_m.group(1) if title_m else "N/A")

stats = re.findall(r'<div[^>]*class="[^"]*stat-[^"]*"[^>]*>(.*?)</div>', dash_html, re.DOTALL)
for s in stats[:5]:
    clean = " ".join(re.sub(r'<[^>]+>', ' ', s).split())
    print("Stat:", clean)

# 3. Scrape Employee Directory
emp_html = opener.open(f"{BASE_URL}/hr/employees").read().decode("utf-8")
print("\n--- Employees currently listed in /hr/employees ---")
# Find employee codes and names
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', emp_html, re.DOTALL)
for r in rows:
    tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
    if tds:
        clean_tds = [" ".join(re.sub(r'<[^>]+>', ' ', td).split()) for td in tds]
        print("  Row:", clean_tds[:4])
