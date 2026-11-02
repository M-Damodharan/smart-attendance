import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import wsgi
from app.db import get_db

app = wsgi.app
app.config["TESTING"] = True
app.config["WTF_CSRF_ENABLED"] = False

results = []
failures = []

def record(name, status, details=""):
    results.append((name, status, details))
    if not status:
        failures.append((name, details))
        print(f"[FAIL] {name} -> {details}")
    else:
        print(f"[PASS] {name} {details}")

with app.app_context():
    client = app.test_client()
    db = get_db()
    
    # Check HR user SBG001
    hr_emp = db.execute("SELECT id, emp_code FROM employees WHERE role='hr' AND active=1 LIMIT 1").fetchone()
    if not hr_emp:
        print("No active HR found in database!")
        sys.exit(1)
    hr_id, hr_code = hr_emp["id"], hr_emp["emp_code"]
    
    # Check Employee user
    reg_emp = db.execute("SELECT id, emp_code FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
    if not reg_emp:
        # fallback to any active user
        reg_emp = db.execute("SELECT id, emp_code FROM employees WHERE active=1 LIMIT 1").fetchone()
    emp_id, emp_code = reg_emp["id"], reg_emp["emp_code"]
    
    print(f"Using HR user: {hr_code} (id={hr_id})")
    print(f"Using Employee user: {emp_code} (id={emp_id})")

    # 1. Test Public Routes
    print("\n--- 1. Testing Public Routes ---")
    public_routes = [
        ("/", [200, 302]),
        ("/login", [200]),
        ("/login/hr", [200]),
        ("/login/employee", [200]),
        ("/healthz", [200]),
        ("/download/apk", [200, 302]),
        ("/sw.js", [200]),
    ]
    for url, allowed in public_routes:
        resp = client.get(url, follow_redirects=False)
        ok = resp.status_code in allowed
        record(f"Public: {url}", ok, f"status={resp.status_code}")

    # 2. Test HR Pages
    print("\n--- 2. Testing HR Pages ---")
    with client.session_transaction() as sess:
        sess["user_id"] = hr_id
        sess["emp_code"] = hr_code
        sess["role"] = "HR"
        sess["name"] = "HR Admin"

    hr_get_routes = [
        "/hr/dashboard",
        "/hr/powerbi-dashboard",
        "/hr/api/powerbi-analytics",
        "/hr/attendance",
        "/hr/employees",
        "/hr/employees/new",
        f"/hr/employees/{emp_id}",
        "/hr/shifts",
        "/hr/approvals",
        "/hr/corrections",
        "/hr/reports",
        "/hr/reports/export",
        f"/hr/reports/employee/{emp_id}/export",
        "/hr/settings",
        "/hr/audit",
        "/hr/missed-punches",
        "/hr/cloud-backup-sync",
        "/hr/face-security-audit",
        "/hr/sunday-working",
        "/hr/sunday-working/export",
        "/hr/late-coming",
        "/hr/payroll",
        "/hr/payroll/setup",
        "/hr/payroll/export",
    ]

    # Find a payroll ID if exists
    pay = db.execute("SELECT id FROM monthly_payroll LIMIT 1").fetchone()
    if pay:
        hr_get_routes.append(f"/hr/payroll/payslip/{pay['id']}")

    for url in hr_get_routes:
        resp = client.get(url, follow_redirects=True)
        ok = resp.status_code == 200
        text = resp.data.decode("utf-8", errors="replace")
        
        # Check for unrendered Jinja syntax or Python exceptions
        syntax_err = ""
        if "Internal Server Error" in text or "500 Internal" in text:
            ok = False
            syntax_err = "Found 500 Internal Server Error in page"
        elif "UndefinedError" in text or "TemplateSyntaxError" in text:
            ok = False
            syntax_err = "Template rendering syntax error"
        
        record(f"HR Page: {url}", ok, f"status={resp.status_code} {syntax_err}")

    # 3. Test Employee / User Pages
    print("\n--- 3. Testing Employee Pages ---")
    with client.session_transaction() as sess:
        sess["user_id"] = emp_id
        sess["emp_code"] = emp_code
        sess["role"] = "Employee"
        sess["name"] = "Test Employee"

    emp_get_routes = [
        "/dashboard",
        "/punch",
        "/history",
        "/history/export",
        "/leave",
        "/od",
        "/permission",
        "/face-change",
        "/change-password",
        "/my-salary",
        "/my-sunday-duty",
        "/late-coming/submit",
    ]

    # If employee has payroll slip
    emp_pay = db.execute("SELECT id FROM monthly_payroll WHERE emp_id=? LIMIT 1", (emp_id,)).fetchone()
    if emp_pay:
        emp_get_routes.append(f"/my-salary/payslip/{emp_pay['id']}")

    for url in emp_get_routes:
        resp = client.get(url, follow_redirects=True)
        ok = resp.status_code == 200
        text = resp.data.decode("utf-8", errors="replace")
        syntax_err = ""
        if "Internal Server Error" in text or "500 Internal" in text:
            ok = False
            syntax_err = "Found 500 Internal Server Error in page"
        elif "UndefinedError" in text or "TemplateSyntaxError" in text:
            ok = False
            syntax_err = "Template rendering syntax error"
        record(f"Employee Page: {url}", ok, f"status={resp.status_code} {syntax_err}")

    print("\n" + "="*50)
    print(f"Total endpoints tested: {len(results)}")
    print(f"Total passes: {len(results) - len(failures)}")
    print(f"Total failures: {len(failures)}")
    if failures:
        print("\nFailure summary:")
        for name, details in failures:
            print(f" - {name}: {details}")
        sys.exit(1)
    else:
        print("ALL PAGES PASSED 100% WITH ZERO ERRORS!")
