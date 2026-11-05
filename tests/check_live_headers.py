import urllib.request
import re

html = urllib.request.urlopen("https://smart-attendance-u7e6.onrender.com/login/staff").read().decode("utf-8")
comments = re.findall(r'<!--(.*?)-->', html, re.DOTALL)
print("Comments found:", comments)

# Also let's check headers
req = urllib.request.urlopen("https://smart-attendance-u7e6.onrender.com/")
print("Headers:")
for k, v in req.headers.items():
    print(f"  {k}: {v}")
