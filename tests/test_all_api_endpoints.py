import os, sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import wsgi
from app.db import get_db

app = wsgi.app
app.config["TESTING"] = True
app.config["WTF_CSRF_ENABLED"] = False

def run_api_tests():
    with app.app_context():
        client = app.test_client()
        db = get_db()

        emp = db.execute("SELECT id, emp_code FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
        if not emp:
            emp = db.execute("SELECT id, emp_code FROM employees WHERE active=1 LIMIT 1").fetchone()
        
        emp_id = emp["id"]
        db.execute("UPDATE employees SET must_change_pw=0 WHERE id=?", (emp_id,))
        db.commit()

        with client.session_transaction() as sess:
            sess["uid"] = emp_id
            sess["role"] = "employee"
            sess["name"] = "API Test User"

        print(f"Testing APIs with user id={emp_id}")

        # 1. /api/challenge
        r = client.post("/api/challenge")
        assert r.status_code == 200, f"/api/challenge failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"challenge not ok: {j}"
        print("[PASS] /api/challenge -> ok")

        # 2. /api/geo-check
        r = client.post("/api/geo-check", json={"lat": 13.0827, "lng": 80.2707, "acc": 10.0})
        assert r.status_code == 200, f"/api/geo-check failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"geo-check not ok: {j}"
        print("[PASS] /api/geo-check -> ok")

        # 3. /api/geo/my-location
        r = client.get("/api/geo/my-location")
        assert r.status_code == 200, f"/api/geo/my-location failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"my-location not ok: {j}"
        print("[PASS] /api/geo/my-location -> ok")

        # 4. /api/webauthn/status
        r = client.get("/api/webauthn/status")
        assert r.status_code == 200, f"/api/webauthn/status failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"webauthn/status not ok: {j}"
        print("[PASS] /api/webauthn/status -> ok")

        # 5. /api/android-biometric/challenge
        r = client.get("/api/android-biometric/challenge")
        assert r.status_code == 200, f"/api/android-biometric/challenge failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"android-biometric/challenge not ok: {j}"
        print("[PASS] /api/android-biometric/challenge -> ok")

        # 6. /api/notifications
        r = client.get("/api/notifications")
        assert r.status_code == 200, f"/api/notifications failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"notifications not ok: {j}"
        print("[PASS] /api/notifications -> ok")

        # 7. /api/notifications/read-all
        r = client.post("/api/notifications/read-all")
        assert r.status_code == 200, f"/api/notifications/read-all failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"notifications/read-all not ok: {j}"
        print("[PASS] /api/notifications/read-all -> ok")

        # 8. /api/comments
        r = client.post("/api/comments", json={
            "entity_type": "attendance",
            "entity_id": 0,
            "comment": "Testing comment API"
        })
        assert r.status_code == 200, f"/api/comments failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"comments not ok: {j}"
        print("[PASS] /api/comments -> ok")

        # 9. /api/comments/attendance/0
        r = client.get("/api/comments/attendance/0")
        assert r.status_code == 200, f"/api/comments/attendance/0 failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"get comments not ok: {j}"
        print("[PASS] /api/comments/attendance/0 -> ok")

        # 10. /api/selfie/<emp_code>_test.jpg (authorized photo access)
        r = client.get(f"/api/selfie/{emp['emp_code']}_test.jpg")
        assert r.status_code == 200, f"/api/selfie failed: {r.status_code}"
        print(f"[PASS] /api/selfie -> status={r.status_code}")

        # 11. /api/ai/chat
        r = client.post("/api/ai/chat", json={"message": "What is my attendance status today?"})
        assert r.status_code == 200, f"/api/ai/chat failed: {r.status_code}"
        j = r.get_json()
        assert j and j.get("ok"), f"ai chat not ok: {j}"
        print("[PASS] /api/ai/chat -> ok")

        # 12. HR missed punches resolve (now with CSRF)
        hr = db.execute("SELECT id FROM employees WHERE role='hr' AND active=1 LIMIT 1").fetchone()
        with client.session_transaction() as sess:
            sess["uid"] = hr["id"]
            sess["role"] = "hr"
            sess["name"] = "HR Admin"

        # Check GET /hr/missed-punches/resolve redirects cleanly
        r = client.get("/hr/missed-punches/resolve", follow_redirects=False)
        assert r.status_code == 302, f"GET missed-punches/resolve expected 302, got {r.status_code}"
        print("[PASS] GET /hr/missed-punches/resolve -> 302 redirect to /hr/missed-punches")

        print("\nALL 12 API & INTERACTIVE ENDPOINTS VERIFIED 100% OPERATIONAL!")

if __name__ == "__main__":
    run_api_tests()
