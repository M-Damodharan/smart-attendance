"""Multi-User Concurrency and Load Balancing Benchmark Test.
Simulates multiple concurrent employees and HR administrators accessing
different pages simultaneously, measuring reload speed and stability.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import concurrent.futures
import time
from app import create_app

app = create_app({"TESTING": False})


def test_concurrent_multi_user_page_reloads():
    with app.app_context():
        from app.db import get_db
        db = get_db()
        hr_emp = db.execute("SELECT id, name FROM employees WHERE role='hr' AND active=1 LIMIT 1").fetchone()
        reg_emp = db.execute("SELECT id, name FROM employees WHERE role='employee' AND active=1 AND must_change_pw=0 LIMIT 1").fetchone()
        if not reg_emp:
            reg_emp = db.execute("SELECT id, name FROM employees WHERE role='employee' AND active=1 LIMIT 1").fetchone()
            if reg_emp:
                db.execute("UPDATE employees SET must_change_pw=0 WHERE id=?", (reg_emp["id"],))
                if hasattr(db, "commit"):
                    db.commit()
        hr_id = hr_emp["id"] if hr_emp else 1
        hr_name = hr_emp["name"] if hr_emp else "HR Admin"
        emp_id = reg_emp["id"] if reg_emp else 2
        emp_name = reg_emp["name"] if reg_emp else "Employee"

    endpoints = [
        ("/hr/dashboard", hr_id, "hr", hr_name),
        ("/hr/attendance", hr_id, "hr", hr_name),
        ("/hr/employees", hr_id, "hr", hr_name),
        ("/hr/cloud-backup-sync", hr_id, "hr", hr_name),
        ("/dashboard", emp_id, "employee", emp_name),
        ("/punch", emp_id, "employee", emp_name),
        ("/history", emp_id, "employee", emp_name),
    ]

    def _fetch_page(task_info):
        url, uid, role, name = task_info
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["uid"] = uid
                sess["role"] = role
                sess["name"] = name
            t0 = time.time()
            resp = client.get(url, headers={"Accept-Encoding": "gzip"})
            elapsed_ms = (time.time() - t0) * 1000.0
            return url, resp.status_code, elapsed_ms, len(resp.data)

    # 1. Warm-up
    _fetch_page(endpoints[0])

    # 2. Simulate 20 concurrent users hitting different pages at the same instant
    tasks = [endpoints[i % len(endpoints)] for i in range(20)]
    t_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(_fetch_page, tasks))
    total_batch_time = (time.time() - t_start) * 1000.0

    print(f"\n=== MULTI-USER LOAD BALANCE CONCURRENCY RESULTS ===")
    print(f"Total concurrent requests processed: {len(results)} in {total_batch_time:.1f} ms")
    latencies = []
    for url, status, ms, size in results:
        latencies.append(ms)
        print(f"  {status} {url:25s} -> {ms:6.1f} ms ({size} bytes)")
        assert status == 200, f"Expected 200 for {url}, got {status}"

    avg_latency = sum(latencies) / len(latencies)
    print(f"\nAverage Concurrent Page Response: {avg_latency:.1f} ms (Min: {min(latencies):.1f} ms, Max: {max(latencies):.1f} ms)")
    assert avg_latency < 1500, f"Average concurrent latency too high: {avg_latency} ms"


if __name__ == "__main__":
    test_concurrent_multi_user_page_reloads()
