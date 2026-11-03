import os, sys, re
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import wsgi
from app.db import get_db

app = wsgi.app
app.config["TESTING"] = True
# Keep WTF_CSRF_ENABLED true to test live CSRF validation!
app.config["WTF_CSRF_ENABLED"] = True

def test_missed_punch_resolve_live_csrf():
    with app.app_context():
        client = app.test_client()
        db = get_db()

        # Find HR user
        hr = db.execute("SELECT id, emp_code FROM employees WHERE role='hr' AND active=1 LIMIT 1").fetchone()
        hr_id, hr_code = hr["id"], hr["emp_code"]
        db.execute("UPDATE employees SET must_change_pw=0 WHERE id=?", (hr_id,))
        db.commit()

        # Find an employee
        emp = db.execute("SELECT id, emp_code FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
        emp_id = emp["id"]

        # Insert a test pending missed punch
        test_date = "2026-11-01"
        db.execute("DELETE FROM attendance WHERE emp_id=? AND date=?", (emp_id, test_date))
        db.execute("""
            INSERT INTO attendance (emp_id, date, shift_id, check_in, check_out, work_hours, ot_hours, status, mp_resolved, remarks)
            VALUES (?, ?, 1, ?, NULL, 0, 0, 'MP', 0, 'Auto Marked - OUT Punch Not Recorded Within 12 Hours.')
        """, (emp_id, test_date, f"{test_date} 08:30:00"))
        db.commit()

        att_row = db.execute("SELECT id FROM attendance WHERE emp_id=? AND date=?", (emp_id, test_date)).fetchone()
        att_id = att_row["id"]
        print(f"Created test pending MP: att_id={att_id} for emp_id={emp_id}")

        # Set HR session
        with client.session_transaction() as sess:
            sess["uid"] = hr_id
            sess["role"] = "hr"
            sess["name"] = "HR Admin"
            sess["csrf"] = "test_csrf_token_secret_12345"

        # 1. Fetch /hr/missed-punches to get page with CSRF token
        resp = client.get("/hr/missed-punches")
        assert resp.status_code == 200, f"Failed loading /hr/missed-punches: {resp.status_code}"
        html = resp.get_data(as_text=True)

        # Extract CSRF token from the form
        csrf_match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html)
        assert csrf_match, "No csrf_token found in /hr/missed-punches HTML!"
        csrf_token = csrf_match.group(1)
        print(f"[PASS] Extracted live CSRF token: {csrf_token[:10]}...")

        # 2. Submit resolution with valid CSRF token -> MUST SUCCEED (302 redirect)
        resolve_resp = client.post("/hr/missed-punches/resolve", data={
            "csrf_token": csrf_token,
            "att_id": str(att_id),
            "action": "correct_out",
            "check_out": "20:30",
            "reason": "Employee machine breakdown prevented biometric punch out. Verified with supervisor."
        }, follow_redirects=False)

        assert resolve_resp.status_code == 302, f"Expected 302 redirect on resolve, got {resolve_resp.status_code}"
        print("[PASS] POST /hr/missed-punches/resolve succeeded with 302 redirect (No 400 error!)")

        # Verify DB update
        updated_att = db.execute("SELECT * FROM attendance WHERE id=?", (att_id,)).fetchone()
        assert updated_att["mp_resolved"] == 1, "mp_resolved was not set to 1!"
        assert updated_att["check_out"] is not None, "check_out was not set!"
        assert updated_att["work_hours"] > 0, "work_hours was not calculated!"
        print(f"[PASS] Attendance updated: check_out={updated_att['check_out']}, hours={updated_att['work_hours']}, status={updated_att['status']}")

        # 3. Insert another test pending MP to test Confirm MP form
        test_date2 = "2026-10-31"
        db.execute("DELETE FROM attendance WHERE emp_id=? AND date=?", (emp_id, test_date2))
        db.execute("""
            INSERT INTO attendance (emp_id, date, shift_id, check_in, check_out, work_hours, ot_hours, status, mp_resolved, remarks)
            VALUES (?, ?, 1, ?, NULL, 0, 0, 'MP', 0, 'Auto Marked - OUT Punch Not Recorded Within 12 Hours.')
        """, (emp_id, test_date2, f"{test_date2} 08:30:00"))
        db.commit()

        att_row2 = db.execute("SELECT id FROM attendance WHERE emp_id=? AND date=?", (emp_id, test_date2)).fetchone()
        att_id2 = att_row2["id"]

        # Confirm MP with CSRF token
        confirm_resp = client.post("/hr/missed-punches/resolve", data={
            "csrf_token": csrf_token,
            "att_id": str(att_id2),
            "action": "approve_mp",
            "reason": "HR confirmed employee left early without permission"
        }, follow_redirects=False)

        assert confirm_resp.status_code == 302, f"Confirm MP expected 302, got {confirm_resp.status_code}"
        updated_att2 = db.execute("SELECT * FROM attendance WHERE id=?", (att_id2,)).fetchone()
        assert updated_att2["mp_resolved"] == 1, "att2 mp_resolved not set!"
        assert updated_att2["status"] == "MP", "att2 status not MP!"
        assert updated_att2["work_hours"] == 0.0, "att2 work_hours not 0!"
        print("[PASS] Confirm MP succeeded with 302 redirect & DB verified!")

        # Clean up test rows
        db.execute("DELETE FROM attendance WHERE id IN (?, ?)", (att_id, att_id2))
        db.commit()

        print("\nMISSED PUNCH RESOLVE CSRF INTEGRATION 100% VERIFIED WORKING!")

if __name__ == "__main__":
    test_missed_punch_resolve_live_csrf()
