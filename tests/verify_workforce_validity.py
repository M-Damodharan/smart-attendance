import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import wsgi
from app.hr import _employees
from app.db import get_db

with wsgi.app.app_context():
    emps = _employees()
    print("=" * 60)
    print("TOTAL EMPLOYEES RETURNED BY _employees():", len(emps))
    print("=" * 60)
    for e in emps:
        print(f"  [{e['emp_code']}] {e['name']:22} | Role: {e['role']:8} | Dept: {e['department']:20} | Cat: {e['category_name']}")
    
    db = get_db()
    test_recs = db.execute("SELECT COUNT(*) as c FROM employees WHERE emp_code IN ('AUDITEMP01', 'LOCALTEST01', 'TESTGEN01')").fetchone()["c"]
    print("\nLeftover test accounts count in Turso Cloud:", test_recs)
    
    hr_found = any(e["emp_code"] == "HR001" for e in emps)
    print("HR Admin (HR001) present in workforce/attendance list:", hr_found)
    assert hr_found, "HR001 must be present in workforce list!"
    assert test_recs == 0, "No test accounts should exist!"
    print("\nALL VERIFICATIONS PASSED 100%!")
