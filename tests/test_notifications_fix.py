import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ["TESTING"] = "1"
os.environ["DEMO_DATA"] = "1"

from app import create_app
from app.db import init_db, get_db
from app.services import create_notification

app = create_app({"TESTING": True, "DATABASE": "instance/test_notifs.db"})

with app.app_context():
    init_db(app)
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE role='employee' LIMIT 1").fetchone()
    hr = db.execute("SELECT * FROM employees WHERE role='hr' LIMIT 1").fetchone()
    print("Testing with Emp:", emp["emp_code"], "HR:", hr["emp_code"])

client = app.test_client()

# Login as HR
with client.session_transaction() as sess:
    sess["uid"] = hr["id"]
    sess["name"] = hr["name"]
    sess["role"] = "hr"
    sess["csrf"] = "testcsrf123456"

csrf_headers = {"X-CSRFToken": "testcsrf123456"}

# Clear any demo seed missed punches so we start fresh
client.post("/api/notifications/clear-all", headers=csrf_headers)

# 1. Initial check (clean state)
res = client.get("/api/notifications")
d = res.get_json()
assert d["ok"] is True
print("1. Clean check -> unread_count:", d["unread_count"], "items:", len(d["notifications"]))
assert d["unread_count"] == 0
assert len(d["notifications"]) == 0

# 2. Create notification for HR
with app.app_context():
    nid = create_notification(
        title="Test Leave",
        message="EMP001 applied for leave",
        kind="leave",
        target_role="hr"
    )

res = client.get("/api/notifications")
d = res.get_json()
assert d["unread_count"] >= 1
assert len(d["notifications"]) >= 1
print("2. After notification created -> unread_count:", d["unread_count"], "items:", len(d["notifications"]))

# 3. Mark read
res_read = client.post(f"/api/notifications/read/{nid}", headers=csrf_headers)
assert res_read.get_json()["ok"] is True

# 4. Check that read message is removed from active list
res_after = client.get("/api/notifications")
d_after = res_after.get_json()
print("3. After single read -> unread_count:", d_after["unread_count"], "items (should be 0):", len(d_after["notifications"]))
assert d_after["unread_count"] == 0
assert len(d_after["notifications"]) == 0

# 5. Check include_all returns it
res_all = client.get("/api/notifications?all=1")
d_all = res_all.get_json()
print("4. With ?all=1 -> items:", len(d_all["notifications"]))
assert len(d_all["notifications"]) >= 1

# 6. Test read-all
with app.app_context():
    create_notification(title="Alert 1", message="msg1", target_role="hr")
    create_notification(title="Alert 2", message="msg2", target_role="hr")

res_unread = client.get("/api/notifications")
assert res_unread.get_json()["unread_count"] == 2

res_read_all = client.post("/api/notifications/read-all", headers=csrf_headers)
assert res_read_all.get_json()["ok"] is True

res_after_read_all = client.get("/api/notifications")
d_ara = res_after_read_all.get_json()
print("5. After read-all -> unread_count:", d_ara["unread_count"], "items (should be 0):", len(d_ara["notifications"]))
assert d_ara["unread_count"] == 0
assert len(d_ara["notifications"]) == 0

# 7. Test delete individual notification
with app.app_context():
    del_id = create_notification(title="Delete Me", message="del msg", target_role="hr")

res_del = client.post(f"/api/notifications/delete/{del_id}", headers=csrf_headers)
assert res_del.get_json()["ok"] is True
res_after_del = client.get("/api/notifications?all=1")
assert not any(n["id"] == del_id for n in res_after_del.get_json()["notifications"])
print("6. After delete single -> successfully deleted")

# 8. Test clear-all
res_clear = client.post("/api/notifications/clear-all", headers=csrf_headers)
assert res_clear.get_json()["ok"] is True
res_all_cleared = client.get("/api/notifications?all=1")
print("7. After clear-all -> total items in DB (should be 0):", len(res_all_cleared.get_json()["notifications"]))
assert len(res_all_cleared.get_json()["notifications"]) == 0

# 9. Test Employee role
with client.session_transaction() as sess:
    sess["uid"] = emp["id"]
    sess["name"] = emp["name"]
    sess["role"] = "employee"
    sess["csrf"] = "testcsrf123456"

with app.app_context():
    create_notification(title="Emp Notice", message="Hello employee", user_id=emp["id"])

res_emp = client.get("/api/notifications")
assert res_emp.get_json()["unread_count"] == 1
assert len(res_emp.get_json()["notifications"]) == 1

res_emp_read = client.post("/api/notifications/read-all", headers=csrf_headers)
assert res_emp_read.get_json()["ok"] is True

res_emp_after = client.get("/api/notifications")
assert res_emp_after.get_json()["unread_count"] == 0
assert len(res_emp_after.get_json()["notifications"]) == 0
print("8. Employee flow -> unread_count:", res_emp_after.get_json()["unread_count"], "items (should be 0):", len(res_emp_after.get_json()["notifications"]))

print("\n>>> ALL NOTIFICATION FIX TESTS PASSED 100%! <<<")
