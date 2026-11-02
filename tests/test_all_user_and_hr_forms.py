import os, sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import wsgi
from datetime import date, timedelta
from app.db import get_db

app = wsgi.app
app.config["TESTING"] = True
app.config["WTF_CSRF_ENABLED"] = False

def run_e2e_verification():
    with app.app_context():
        client = app.test_client()
        db = get_db()
        t = date.today()

        hr = db.execute("SELECT id, emp_code FROM employees WHERE role='hr' AND active=1 LIMIT 1").fetchone()
        emp = db.execute("SELECT id, emp_code FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()

        hr_id, hr_code = hr["id"], hr["emp_code"]
        emp_id, emp_code = emp["id"], emp["emp_code"]

        db.execute("UPDATE employees SET must_change_pw=0 WHERE id IN (?, ?)", (hr_id, emp_id))
        db.commit()

        print(f"Testing with HR={hr_code} (id={hr_id}), Emp={emp_code} (id={emp_id})")

        # -------------------------------------------------------------
        # 1. Employee Late Coming Form Submission
        # -------------------------------------------------------------
        with client.session_transaction() as sess:
            sess["uid"] = emp_id
            sess["role"] = "employee"
            sess["name"] = "Test Emp"

        resp = client.post("/late-coming/submit", data={
            "date": t.strftime("%Y-%m-%d"),
            "reason": "Traffic jam on outer ring road"
        }, follow_redirects=True)
        assert resp.status_code == 200, f"Late submit failed: {resp.status_code}"
        
        req = db.execute("SELECT * FROM late_coming_requests WHERE emp_id=? ORDER BY id DESC LIMIT 1", (emp_id,)).fetchone()
        assert req is not None, "Late coming request was not inserted into database!"
        req_id = req["id"]
        print(f"[PASS] Employee Late Coming submitted -> id={req_id}")

        # -------------------------------------------------------------
        # 2. HR Late Coming Approval
        # -------------------------------------------------------------
        with client.session_transaction() as sess:
            sess["uid"] = hr_id
            sess["role"] = "hr"
            sess["name"] = "HR Manager"

        resp = client.post(f"/hr/late-coming/decide/{req_id}", data={
            "action": "Approved",
            "hr_remarks": "Traffic verified. Late penalty waived."
        }, follow_redirects=True)
        assert resp.status_code == 200, f"Late decide failed: {resp.status_code}"

        req_updated = db.execute("SELECT status, hr_remarks FROM late_coming_requests WHERE id=?", (req_id,)).fetchone()
        assert req_updated["status"] == "Approved", f"Expected Approved, got {req_updated['status']}"
        print(f"[PASS] HR Late Coming approved -> status={req_updated['status']}")

        # -------------------------------------------------------------
        # 3. HR Sunday Duty: Assign -> Record Punch -> Approve
        # -------------------------------------------------------------
        next_sunday = t + timedelta(days=(6 - t.weekday()) % 7)
        sun_str = next_sunday.strftime("%Y-%m-%d")

        resp = client.post("/hr/sunday-working/assign", data={
            "sunday_date": sun_str,
            "emp_ids": [str(emp_id)],
            "shift_id": "1",
            "duty_type": "Production Maintenance"
        }, follow_redirects=True)
        assert resp.status_code == 200, f"Sunday assign failed: {resp.status_code}"

        sw = db.execute("SELECT * FROM sunday_working WHERE emp_id=? AND sunday_date=?", (emp_id, sun_str)).fetchone()
        assert sw is not None, "Sunday assignment record not found in DB!"
        sw_id = sw["id"]
        print(f"[PASS] Sunday Working assigned -> sw_id={sw_id}")

        # Record manual punch for Sunday
        resp = client.post("/hr/sunday-working/record-punch", data={
            "sw_id": str(sw_id),
            "check_in": f"{sun_str} 08:30:00",
            "check_out": f"{sun_str} 17:30:00",
            "work_hours": "9.0",
            "ot_hours": "1.0",
            "status": "Completed"
        }, follow_redirects=True)
        assert resp.status_code == 200, f"Sunday record punch failed: {resp.status_code}"

        # Approve Sunday Duty
        resp = client.post(f"/hr/sunday-working/approve/{sw_id}", data={
            "action": "Approved",
            "hr_remarks": "Overtime verified on machine line 2"
        }, follow_redirects=True)
        assert resp.status_code == 200, f"Sunday approve failed: {resp.status_code}"
        
        sw_upd = db.execute("SELECT status FROM sunday_working WHERE id=?", (sw_id,)).fetchone()
        assert sw_upd["status"] == "Approved", f"Expected Approved, got {sw_upd['status']}"
        print(f"[PASS] Sunday Working punch recorded & approved -> status={sw_upd['status']}")

        # -------------------------------------------------------------
        # 4. HR Payroll Setup & Calculation
        # -------------------------------------------------------------
        resp = client.post("/hr/payroll/setup", data={
            "emp_id": [str(emp_id)],
            f"basic_{emp_id}": "28000.00",
            f"quota_{emp_id}": "2.0",
            f"days_{emp_id}": "30",
            f"ot_rate_{emp_id}": "150.00",
            f"sun_rate_{emp_id}": "933.33",
            f"allowance_{emp_id}": "1000.00",
            f"deduction_{emp_id}": "200.00",
            f"pay_mode_{emp_id}": "Bank Transfer",
            f"bank_name_{emp_id}": "HDFC Bank",
            f"account_no_{emp_id}": "50100234567890",
            f"ifsc_code_{emp_id}": "HDFC0001234"
        }, follow_redirects=True)
        assert resp.status_code == 200, f"Salary setup post failed: {resp.status_code}"
        print("[PASS] HR Payroll setup saved")

        cur_month = t.strftime("%Y-%m")
        resp = client.post("/hr/payroll/calculate", data={"month": cur_month}, follow_redirects=True)
        assert resp.status_code == 200, f"Payroll calculation failed: {resp.status_code}"

        p = db.execute("SELECT * FROM monthly_payroll WHERE emp_id=? AND month=?", (emp_id, cur_month)).fetchone()
        assert p is not None, "Monthly payroll record was not calculated!"
        pay_id = p["id"]
        print(f"[PASS] HR Payroll calculated -> id={pay_id}, net_salary={p['net_salary']}")

        # Approve Payroll
        resp = client.post(f"/hr/payroll/approve/{pay_id}", data={
            "action": "Approved",
            "remarks": "Audited and approved for bank release"
        }, follow_redirects=True)
        assert resp.status_code == 200, f"Payroll approve failed: {resp.status_code}"

        p_upd = db.execute("SELECT status FROM monthly_payroll WHERE id=?", (pay_id,)).fetchone()
        assert p_upd["status"] == "Approved", f"Expected Approved, got {p_upd['status']}"
        print(f"[PASS] HR Payroll approved -> status={p_upd['status']}")

        # -------------------------------------------------------------
        # 5. Employee Self-Service Verification
        # -------------------------------------------------------------
        with client.session_transaction() as sess:
            sess["uid"] = emp_id
            sess["role"] = "employee"
            sess["name"] = "Test Emp"

        # Employee payslip view
        resp = client.get(f"/my-salary/payslip/{pay_id}")
        assert resp.status_code == 200, f"Employee payslip view failed: {resp.status_code}"
        assert b"SRI BALAJI GEARS" in resp.data
        print("[PASS] Employee payslip rendered successfully")

        # Employee Sunday duty view
        resp = client.get("/my-sunday-duty")
        assert resp.status_code == 200, f"Employee Sunday duty view failed: {resp.status_code}"
        print("[PASS] Employee Sunday duty portal rendered successfully")

        # -------------------------------------------------------------
        # 6. AI Assistant Query Handling
        # -------------------------------------------------------------
        from app.ai_assistant import process_ai_query
        queries = [
            "How many employees are absent today?",
            "Who is working right now?",
            "Show me today's absent list",
            "What is the Sunday duty schedule?",
            "Show me monthly salary status"
        ]
        user_dict = {"id": hr_id, "emp_code": hr_code, "name": "HR Manager", "role": "hr"}
        for q in queries:
            res = process_ai_query(user_dict, q)
            assert isinstance(res, dict) and "reply" in res, f"Expected dict with reply, got {res}"
            reply_text = res["reply"]
            assert len(reply_text) > 10, f"Reply too short for query {q}: {reply_text}"
            clean_reply = reply_text.replace("\n", " ")[:60].encode("ascii", "replace").decode("ascii")
            print(f"[PASS] AI Query: '{q}' -> {clean_reply}...")

        # Also test via HTTP POST /ai/chat
        with client.session_transaction() as sess:
            sess["uid"] = hr_id
            sess["role"] = "hr"
            sess["name"] = "HR Manager"

        resp = client.post("/api/ai/chat", json={"message": "Show me monthly salary status"})
        assert resp.status_code == 200, f"/api/ai/chat failed: {resp.status_code}"
        j = resp.get_json()
        assert j.get("ok") is True, f"/api/ai/chat ok!=True: {j}"
        print(f"[PASS] HTTP /api/ai/chat endpoint verified -> ok=True")

        print("\nALL WORKFLOWS, FORMS, AND DATA ACTIONS VERIFIED 100% OPERATIONAL!")

if __name__ == "__main__":
    run_e2e_verification()
