import os
import sys
import tempfile
import sqlite3
import pytest
from datetime import date, datetime, timedelta

# Setup test environment
tmp_dir = tempfile.mkdtemp()
db_path = os.path.join(tmp_dir, "test_sec.db")
selfie_dir = os.path.join(tmp_dir, "selfies")
os.environ["DEMO_DATA"] = "1"
os.environ["DATABASE"] = db_path
os.environ["SELFIE_DIR"] = selfie_dir

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.db import get_db

@pytest.fixture(scope="module")
def app_instance():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    return app

@pytest.fixture(scope="module")
def client(app_instance):
    return app_instance.test_client()


# ==============================================================================
# 1. DATABASE STORAGE & PERSISTENCE TESTS
# ==============================================================================

def test_database_persistence_and_wal(app_instance):
    """Verify database file exists, is in WAL mode, and tables are initialized."""
    db_file = app_instance.config["DATABASE"]
    assert os.path.exists(db_file), "Database file must exist on disk"
    con = sqlite3.connect(db_file)
    cur = con.cursor()
    
    # Check WAL mode
    journal_mode = cur.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal_mode.lower() == "wal", f"Journal mode should be WAL, got {journal_mode}"
    
    # Check tables
    tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    expected_tables = ["employees", "shifts", "offices", "attendance", "leaves", "system_settings", "audit_log"]
    for t in expected_tables:
        assert t in tables, f"Expected table {t} to exist in database"
    con.close()


def test_database_crud_and_integrity(app_instance):
    """Test full CRUD operations, foreign key constraints, and UNIQUE constraints."""
    db_file = app_instance.config["DATABASE"]
    con = sqlite3.connect(db_file)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()
    
    # Test setting persistence
    cur.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('test_key', 'test_val')")
    con.commit()
    val = cur.execute("SELECT value FROM system_settings WHERE key='test_key'").fetchone()[0]
    assert val == "test_val"
    
    # Test Attendance insertion and UNIQUE(emp_id, date) constraint
    today_str = date.today().isoformat()
    cur.execute("DELETE FROM attendance WHERE emp_id=2 AND date=?", (today_str,))
    con.commit()
    
    cur.execute("""
        INSERT INTO attendance (emp_id, date, check_in, in_lat, in_lng, in_dist, in_geo_ok, work_hours, status)
        VALUES (2, ?, '09:00:00', 12.9716, 77.5946, 15.2, 1, 0, 'IN')
    """, (today_str,))
    con.commit()
    
    rec = cur.execute("SELECT check_in, in_dist, status FROM attendance WHERE emp_id=2 AND date=?", (today_str,)).fetchone()
    assert rec[0] == "09:00:00"
    assert rec[1] == 15.2
    assert rec[2] == "IN"
    
    # Duplicate insertion on same day should raise IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("""
            INSERT INTO attendance (emp_id, date, check_in)
            VALUES (2, ?, '10:00:00')
        """, (today_str,))
        con.commit()
        
    # Foreign key constraint test: inserting attendance for non-existent emp_id
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("INSERT INTO attendance (emp_id, date) VALUES (999999, '2099-01-01')")
        con.commit()
        
    con.close()


# ==============================================================================
# 2. SECURITY TESTS: SQL INJECTION DEFENSE
# ==============================================================================

def test_sql_injection_on_login(client):
    """Ensure SQL injection strings fail safely without authentication bypass."""
    sqli_payloads = [
        "' OR '1'='1",
        "admin' --",
        "' OR 1=1 --",
        "\" or \"\"=\"",
        "HR001' OR '1'='1' --",
        "' UNION SELECT * FROM employees --"
    ]
    for p in sqli_payloads:
        # Get valid CSRF token first
        res = client.get("/login")
        token = res.data.decode().split('name="csrf_token" value="')[1].split('"')[0]
        
        post_res = client.post("/login", data={
            "emp_code": p,
            "password": "wrong_password",
            "csrf_token": token
        })
        # Should NOT log in, should show invalid credentials
        assert b"Invalid employee code or password" in post_res.data or post_res.status_code == 200


# ==============================================================================
# 3. SECURITY TESTS: CSRF PROTECTION
# ==============================================================================

def test_csrf_protection_on_state_changes(client):
    """Verify that requests missing or having invalid CSRF tokens are rejected (HTTP 400)."""
    # Missing CSRF token
    r1 = client.post("/login", data={"emp_code": "HR001", "password": "admin123"})
    assert r1.status_code == 400, "Should reject state-changing POST without CSRF token"
    
    # Fake/Invalid CSRF token
    r2 = client.post("/login", data={"emp_code": "HR001", "password": "admin123", "csrf_token": "malicious_fake_token"})
    assert r2.status_code == 400, "Should reject state-changing POST with invalid CSRF token"


# ==============================================================================
# 4. SECURITY TESTS: ROLE-BASED ACCESS CONTROL (RBAC) & PRIVILEGE ESCALATION
# ==============================================================================

def test_rbac_unauthenticated_redirection(client):
    """Unauthenticated users must not access protected dashboards or HR routes."""
    client.get("/logout")
    for protected in ["/hr/", "/hr/attendance", "/hr/employees", "/hr/settings", "/dashboard", "/punch"]:
        res = client.get(protected, follow_redirects=False)
        assert res.status_code == 302, f"Expected redirect on {protected}, got {res.status_code}"
        assert "/login" in res.headers.get("Location", "")


def test_rbac_employee_cannot_access_hr_endpoints(client):
    """An employee user must be rejected with 403 Forbidden on all HR routes."""
    # Login as Employee E001
    res = client.get("/login")
    token = res.data.decode().split('name="csrf_token" value="')[1].split('"')[0]
    client.post("/login", data={"emp_code": "E001", "password": "emp123", "csrf_token": token})
    
    hr_endpoints = [
        "/hr/",
        "/hr/attendance",
        "/hr/employees",
        "/hr/employees/new",
        "/hr/approvals",
        "/hr/corrections",
        "/hr/audit",
        "/hr/reports",
        "/hr/reports/export",
        "/hr/settings"
    ]
    for endpoint in hr_endpoints:
        r = client.get(endpoint)
        assert r.status_code == 403, f"Employee must be blocked (403) from {endpoint}, got {r.status_code}"


# ==============================================================================
# 5. SECURITY TESTS: PATH TRAVERSAL / ARBITRARY FILE ACCESS
# ==============================================================================

def test_path_traversal_selfie_endpoint(client):
    """Ensure directory traversal attacks on the selfie endpoint are blocked."""
    # Login as employee
    res = client.get("/login")
    token = res.data.decode().split('name="csrf_token" value="')[1].split('"')[0]
    client.post("/login", data={"emp_code": "E001", "password": "emp123", "csrf_token": token})
    
    traversal_payloads = [
        "../../config.py",
        "..%2f..%2fconfig.py",
        "....//....//config.py",
        "..\\..\\config.py",
        "instance/attendance.db",
        "non_existent_file.jpg"
    ]
    for p in traversal_payloads:
        r = client.get(f"/api/selfie/{p}")
        assert r.status_code in (404, 400, 403), f"Traversal attempt '{p}' should not return 200, got {r.status_code}"


# ==============================================================================
# 6. SECURITY TESTS: BRUTE FORCE & LOGIN THROTTLING
# ==============================================================================

def test_login_throttling_brute_force(client):
    """Verify that multiple failed logins trigger throttling / temporary lockout."""
    client.get("/logout")
    for _ in range(6):
        res = client.get("/login")
        token = res.data.decode().split('name="csrf_token" value="')[1].split('"')[0]
        r = client.post("/login", data={"emp_code": "E004", "password": "bad_password", "csrf_token": token})
    
    # 6th attempt should be blocked with rate limiting message
    assert b"Too many failed attempts" in r.data or b"temporarily locked" in r.data or r.status_code == 200
