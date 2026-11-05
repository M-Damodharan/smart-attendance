import urllib.request
import urllib.parse
import http.cookiejar
import re

BASE_URL = "https://smart-attendance-u7e6.onrender.com"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Login
html = opener.open(f"{BASE_URL}/login/staff").read().decode("utf-8")
csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)
pdata = urllib.parse.urlencode({"emp_code": "HR001", "password": "123456789", "login_type": "staff", "csrf_token": csrf}).encode()
opener.open(urllib.request.Request(f"{BASE_URL}/login/staff", data=pdata, headers={"User-Agent": "Mozilla/5.0", "Referer": f"{BASE_URL}/login/staff"}))

print("=" * 60)
print("1. EMPLOYEES ON /hr/employees")
print("=" * 60)
emp_html = opener.open(f"{BASE_URL}/hr/employees").read().decode("utf-8")
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', emp_html, re.DOTALL)
for r in rows:
    tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
    if tds:
        print("  " + " | ".join([" ".join(re.sub(r'<[^>]+>', ' ', td).split()) for td in tds[:4]]))

print("\n" + "=" * 60)
print("2. ATTENDANCE ROWS ON /hr/attendance")
print("=" * 60)
att_html = opener.open(f"{BASE_URL}/hr/attendance").read().decode("utf-8")
att_rows = re.findall(r'<tr[^>]*class="[^"]*att-row[^"]*"[^>]*>(.*?)</tr>', att_html, re.DOTALL)
for r in att_rows:
    tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
    if tds:
        print("  " + " | ".join([" ".join(re.sub(r'<[^>]+>', ' ', td).split()) for td in tds[:4]]))

print("\n" + "=" * 60)
print("3. DASHBOARD RECENT PUNCHES & ATTENDANCE ON /hr/dashboard")
print("=" * 60)
dash_html = opener.open(f"{BASE_URL}/hr/dashboard").read().decode("utf-8")
d_rows = re.findall(r'<tr[^>]*>(.*?)</tr>', dash_html, re.DOTALL)
for r in d_rows[:10]:
    tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
    if tds:
        print("  " + " | ".join([" ".join(re.sub(r'<[^>]+>', ' ', td).split()) for td in tds[:4]]))
