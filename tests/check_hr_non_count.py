import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import wsgi
from app.hr import _employees
from app.db import get_db

with wsgi.app.app_context():
    emps = _employees()
    print("=" * 60)
    print("TOTAL EMPLOYEES COUNTED IN ATTENDANCE / PRESENT LISTS:", len(emps))
    print("=" * 60)
    for e in emps:
        print(f"  [{e['emp_code']}] {e['name']} | Role: {e['role']}")
    
    db = get_db()
    all_emps = db.execute("SELECT emp_code, name, role FROM employees").fetchall()
    print("\nTotal accounts in Master Database:", len(all_emps))
    for e in all_emps:
        print(f"  [{e['emp_code']}] {e['name']} | Role: {e['role']}")

    hr_in_attendance = any(e["emp_code"] == "HR001" for e in emps)
    print("\nIs HR001 counted in attendance / present list?", hr_in_attendance)
    assert not hr_in_attendance, "HR001 should NOT be counted in attendance list!"
    print("VERIFICATION SUCCESS: HR Admin is NOT counted in attendance / present lists!")
