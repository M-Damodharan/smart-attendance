import pytest
from app import create_app
from app.db import get_db

@pytest.fixture
def app():
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "DATABASE": "instance/attendance.db", "USE_TURSO": False})
    return app

@pytest.fixture
def client(app):
    return app.test_client()

def login_hr(client, app):
    with app.app_context():
        db = get_db()
        hr = db.execute("SELECT * FROM employees WHERE role='hr' LIMIT 1").fetchone()
        assert hr is not None, "HR account must exist"
        hr_id = hr["id"]
        db.execute("UPDATE employees SET must_change_pw=0 WHERE id=?", (hr_id,))
        db.commit()
    with client.session_transaction() as sess:
        sess["uid"] = hr_id
        sess["_fresh"] = True
    return hr_id

def login_emp(client, app):
    with app.app_context():
        db = get_db()
        emp = db.execute("SELECT * FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
        assert emp is not None, "Employee account must exist"
        emp_id = emp["id"]
        db.execute("UPDATE employees SET must_change_pw=0 WHERE id=?", (emp_id,))
        db.commit()
    with client.session_transaction() as sess:
        sess["uid"] = emp_id
        sess["_fresh"] = True
    return emp_id

# -----------------------------------------------------------------------------
# Module 1: Power BI Attendance Analytics
# -----------------------------------------------------------------------------
def test_powerbi_dashboard(client, app):
    login_hr(client, app)
    res = client.get("/hr/powerbi-dashboard")
    assert res.status_code == 200
    assert b"Power BI Dashboard" in res.data
    assert b"presentTrendChart" in res.data
    assert b"absentTrendChart" in res.data

def test_powerbi_analytics_api(client, app):
    login_hr(client, app)
    res = client.get("/hr/api/powerbi-analytics")
    assert res.status_code == 200
    json_data = res.get_json()
    assert json_data["success"] is True
    assert "kpis" in json_data
    assert "present" in json_data["kpis"]
    assert "absent" in json_data["kpis"]
    assert "daily_hourly" in json_data
    assert "weekly" in json_data
    assert "monthly" in json_data
    assert "departments" in json_data
    assert "shifts" in json_data

# -----------------------------------------------------------------------------
# Module 2: Sunday Working Management
# -----------------------------------------------------------------------------
def test_sunday_working_lifecycle(client, app):
    login_hr(client, app)
    # 1. View Sunday Working Page
    res = client.get("/hr/sunday-working")
    assert res.status_code == 200
    assert b"Sunday Working Duty Management" in res.data

    # 2. Assign Sunday Duty
    with app.app_context():
        db = get_db()
        emp = db.execute("SELECT id FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
        emp_id = emp["id"]

    res_post = client.post("/hr/sunday-working/assign", data={
        "emp_ids": [str(emp_id)],
        "sunday_date": "2026-11-08",
        "shift_id": "1",
        "duty_type": "Full Day",
        "remarks": "Test Sunday Gear Hobbing Run"
    }, follow_redirects=True)
    assert res_post.status_code == 200
    assert b"Successfully scheduled Sunday duty" in res_post.data

    # 3. Check DB
    with app.app_context():
        db = get_db()
        sw = db.execute("SELECT * FROM sunday_working WHERE emp_id=? AND sunday_date='2026-11-08'", (emp_id,)).fetchone()
        assert sw is not None
        sw_id = sw["id"]

    # 4. Record punch
    res_punch = client.post("/hr/sunday-working/record-punch", data={
        "sw_id": str(sw_id),
        "check_in": "2026-11-08 08:05:00",
        "check_out": "2026-11-08 20:00:00",
        "work_hours": "11.9",
        "ot_hours": "1.9",
        "status": "Completed"
    }, follow_redirects=True)
    assert res_punch.status_code == 200
    assert b"Sunday attendance punch recorded successfully" in res_punch.data

    # 5. Approve Sunday Duty
    res_appr = client.post(f"/hr/sunday-working/approve/{sw_id}", data={
        "action": "Approved",
        "hr_remarks": "Approved by HR Admin"
    }, follow_redirects=True)
    assert res_appr.status_code == 200
    assert b"marked as Approved" in res_appr.data

    # 6. Export CSV
    res_exp = client.get("/hr/sunday-working/export?date=2026-11-08")
    assert res_exp.status_code == 200
    assert b"Sunday Date" in res_exp.data

    # 7. Employee Self-Service Sunday View
    login_emp(client, app)
    res_emp = client.get("/my-sunday-duty")
    assert res_emp.status_code == 200
    assert b"My Sunday Duty Schedule" in res_emp.data

# -----------------------------------------------------------------------------
# Module 4: Late Coming Approval System
# -----------------------------------------------------------------------------
def test_late_coming_lifecycle(client, app):
    emp_id = login_emp(client, app)
    # 1. Employee late submit page
    res_page = client.get("/late-coming/submit")
    assert res_page.status_code == 200
    assert b"Late Coming" in res_page.data

    # 2. Employee submits late explanation
    res_submit = client.post("/late-coming/submit", data={
        "date": "2026-11-02",
        "reason": "Heavy traffic gridlock on bridge"
    }, follow_redirects=True)
    assert res_submit.status_code == 200
    assert b"submitted to HR" in res_submit.data

    # 3. HR views late coming dashboard
    login_hr(client, app)
    res_hr = client.get("/hr/late-coming?tab=pending")
    assert res_hr.status_code == 200
    assert b"Heavy traffic gridlock" in res_hr.data

    # 4. HR approves late coming
    with app.app_context():
        db = get_db()
        req = db.execute("SELECT id FROM late_coming_requests WHERE emp_id=? ORDER BY id DESC LIMIT 1", (emp_id,)).fetchone()
        assert req is not None
        req_id = req["id"]

    res_decide = client.post(f"/hr/late-coming/decide/{req_id}", data={
        "status": "Approved",
        "hr_remarks": "Traffic delay excused"
    }, follow_redirects=True)
    assert res_decide.status_code == 200
    assert b"marked as Approved" in res_decide.data

# -----------------------------------------------------------------------------
# Module 5: Monthly Salary Calculation & Payroll
# -----------------------------------------------------------------------------
def test_payroll_lifecycle(client, app):
    login_hr(client, app)
    # 1. Payroll page
    res = client.get("/hr/payroll?month=2026-11")
    assert res.status_code == 200
    assert b"Monthly Salary Calculation" in res.data

    # 2. Salary Setup
    res_setup = client.get("/hr/payroll/setup")
    assert res_setup.status_code == 200
    assert b"Employee Compensation Profiles" in res_setup.data

    # 3. Run Payroll Calculation
    res_calc = client.post("/hr/payroll/calculate", data={"month": "2026-11"}, follow_redirects=True)
    assert res_calc.status_code == 200
    assert b"calculation complete" in res_calc.data

    # 4. Check calculated records in DB
    with app.app_context():
        db = get_db()
        pay = db.execute("SELECT * FROM monthly_payroll WHERE month='2026-11' LIMIT 1").fetchone()
        assert pay is not None
        pay_id = pay["id"]
        assert pay["basic_salary"] > 0
        assert pay["per_day_salary"] > 0
        assert pay["net_salary"] > 0

    # 5. HR Approves Payroll record
    res_appr = client.post(f"/hr/payroll/approve/{pay_id}", data={
        "action": "Approved",
        "remarks": "Approved by HR"
    }, follow_redirects=True)
    assert res_appr.status_code == 200
    assert b"marked as Approved" in res_appr.data

    # 6. View Official Printable Payslip
    res_slip = client.get(f"/hr/payroll/payslip/{pay_id}")
    assert res_slip.status_code == 200
    assert b"SRI BALAJI GEARS" in res_slip.data
    assert b"Net Payable Salary" in res_slip.data

    # 7. Export Payroll CSV
    res_exp = client.get("/hr/payroll/export?month=2026-11")
    assert res_exp.status_code == 200
    assert b"Basic Salary" in res_exp.data

    # 8. Employee views My Salary
    login_emp(client, app)
    res_my_sal = client.get("/my-salary")
    assert res_my_sal.status_code == 200
    assert b"My Salary" in res_my_sal.data

# -----------------------------------------------------------------------------
# Module 3: AI Assistant Enhanced Queries
# -----------------------------------------------------------------------------
def test_ai_assistant_reports(client, app):
    login_hr(client, app)
    queries = [
        "generate daily attendance report",
        "generate weekly report",
        "generate monthly report",
        "show missed punches list",
        "check duplicate attendance and suspicious activity",
        "show department summary",
        "sunday working duty summary",
        "salary summary"
    ]
    for q in queries:
        res = client.post("/api/ai/chat", json={"message": q})
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is True
        assert len(data["reply"]) > 20
        # Ensure never using dummy or mock text
        assert "dummy" not in data["reply"].lower()
        assert "mock" not in data["reply"].lower()
