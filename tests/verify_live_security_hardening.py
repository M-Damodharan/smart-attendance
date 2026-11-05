import urllib.request
import urllib.error
import time

print("Waiting for Render deployment of commit 5e9dbc4 (new security headers)...")

for attempt in range(1, 35):
    try:
        req = urllib.request.Request(
            "https://smart-attendance-u7e6.onrender.com/login",
            headers={"User-Agent": "SecurityProbe/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            headers = dict(resp.headers)
            hsts = headers.get("Strict-Transport-Security") or headers.get("strict-transport-security")
            xss = headers.get("X-XSS-Protection") or headers.get("x-xss-protection")
            if hsts and xss:
                print(f"[+] Render deployment is LIVE at attempt {attempt}!")
                print(f"    Strict-Transport-Security: {hsts}")
                print(f"    X-XSS-Protection: {xss}")
                print(f"    X-Content-Type-Options: {headers.get('X-Content-Type-Options')}")
                print(f"    X-Frame-Options: {headers.get('X-Frame-Options')}")
                print(f"    Permissions-Policy: {headers.get('Permissions-Policy')}")
                break
            else:
                print(f"Attempt {attempt}: Deployment still in progress (headers not updated yet)...")
    except Exception as e:
        print(f"Attempt {attempt}: Waiting for server response ({e})...")
    time.sleep(8)
else:
    print("[!] Timeout waiting for new deployment.")

print("\n--- Testing Live Security Defenses Against Attacks ---")

# 1. SQL Injection Probe
try:
    bad_req = urllib.request.Request(
        "https://smart-attendance-u7e6.onrender.com/login?search=%27+UNION+SELECT+*+FROM+employees+--",
        headers={"User-Agent": "SecurityProbe/1.0"}
    )
    urllib.request.urlopen(bad_req, timeout=10)
    print("[-] SQLi Probe FAILED: Not blocked!")
except urllib.error.HTTPError as e:
    print(f"[+] SQLi Probe BLOCKED: HTTP {e.code} Forbidden (WAF Active)")

# 2. XSS Probe
try:
    bad_req = urllib.request.Request(
        "https://smart-attendance-u7e6.onrender.com/login?test=%3Cscript%3Ealert(1)%3C/script%3E",
        headers={"User-Agent": "SecurityProbe/1.0"}
    )
    urllib.request.urlopen(bad_req, timeout=10)
    print("[-] XSS Probe FAILED: Not blocked!")
except urllib.error.HTTPError as e:
    print(f"[+] XSS Probe BLOCKED: HTTP {e.code} Forbidden (WAF Active)")

# 3. Path Traversal Probe
try:
    bad_req = urllib.request.Request(
        "https://smart-attendance-u7e6.onrender.com/login?file=../../../../etc/passwd",
        headers={"User-Agent": "SecurityProbe/1.0"}
    )
    urllib.request.urlopen(bad_req, timeout=10)
    print("[-] Path Traversal Probe FAILED: Not blocked!")
except urllib.error.HTTPError as e:
    print(f"[+] Path Traversal Probe BLOCKED: HTTP {e.code} Forbidden (WAF Active)")

# 4. Unauthenticated Admin Route Probe
try:
    bad_req = urllib.request.Request(
        "https://smart-attendance-u7e6.onrender.com/hr/settings",
        headers={"User-Agent": "SecurityProbe/1.0"}
    )
    urllib.request.urlopen(bad_req, timeout=10)
    print("[-] Unauthenticated Admin Route FAILED: Accessible!")
except urllib.error.HTTPError as e:
    # 302 redirect to /login or 403
    print(f"[+] Unauthenticated Admin Route PROTECTED: HTTP {e.code}")

# 5. Health Check
try:
    req = urllib.request.Request(
        "https://smart-attendance-u7e6.onrender.com/healthz",
        headers={"User-Agent": "SecurityProbe/1.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"[+] System Health Check: HTTP {resp.status} (Healthy)")
except Exception as e:
    print(f"[-] Health Check failed: {e}")
