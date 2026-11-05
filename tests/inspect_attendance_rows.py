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
print("ATTENDANCE ROWS ON /hr/attendance")
print("=" * 60)
att_html = opener.open(f"{BASE_URL}/hr/attendance").read().decode("utf-8")
att_rows = re.findall(r'<tr[^>]*class="[^"]*att-row[^"]*"[^>]*>(.*?)</tr>', att_html, re.DOTALL)
for r in att_rows:
    tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
    if tds:
        clean_tds = [" ".join(re.sub(r'<[^>]+>', ' ', td).split()) for td in tds[:4]]
        line = " | ".join(clean_tds)
        # Avoid Windows console cp1252 encode errors
        print(line.encode("ascii", "replace").decode("ascii"))
