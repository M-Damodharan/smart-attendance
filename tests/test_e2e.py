import os, sys, io, base64, tempfile, re, random
os.environ["DEMO_DATA"] = "1"
tmp = tempfile.mkdtemp()
os.environ["DATABASE"] = os.path.join(tmp, "t.db"); os.environ["SELFIE_DIR"] = os.path.join(tmp, "s")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
import numpy as np
from app import create_app
from app.db import get_db
app = create_app(); app.config["MIN_PUNCH_GAP_MIN"] = 0
c = app.test_client()

def tok(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1) or None
def csrf_from(path):
    return tok(c.get(path).get_data(as_text=True))
def img(seed, size=(160,120), noisy=True):
    rng = np.random.default_rng(seed)
    a = rng.integers(0,255,(size[1],size[0],3),dtype=np.uint8)
    b = io.BytesIO(); Image.fromarray(a).save(b,"JPEG"); return "data:image/jpeg;base64,"+base64.b64encode(b.getvalue()).decode()
def static_img():
    a = np.full((120,160,3),128,dtype=np.uint8); b = io.BytesIO(); Image.fromarray(a).save(b,"JPEG")
    return "data:image/jpeg;base64,"+base64.b64encode(b.getvalue()).decode()

def login(code, pw):
    c.get("/logout"); t = csrf_from("/login")
    return c.post("/login", data={"emp_code":code,"password":pw,"csrf_token":t})

# --- CSRF enforced
r = c.post("/login", data={"emp_code":"E001","password":"emp123"}); assert r.status_code == 400, r.status_code
print("csrf ok")
# --- wrong pw
r = login("E001","bad"); assert b"Invalid" in r.data
# --- HR forced pw change
r = login("HR001","admin123"); assert r.status_code == 302
r = c.get("/hr/", follow_redirects=False); assert "/change-password" in r.headers["Location"]
t = csrf_from("/change-password")
r = c.post("/change-password", data={"current":"admin123","new":"Admin@1234","confirm":"Admin@1234","csrf_token":t}); assert r.status_code==302
for p in ["/hr/","/hr/employees","/hr/employees/new","/hr/attendance","/hr/approvals","/hr/corrections","/hr/audit","/hr/reports","/hr/settings"]:
    r = c.get(p); assert r.status_code==200, (p, r.status_code)
print("hr pages ok")
# employee cannot access hr
login("E001","emp123")
assert c.get("/hr/").status_code == 403
for p in ["/dashboard","/punch","/history","/leave","/od"]:
    r = c.get(p); assert r.status_code==200,(p,r.status_code,r.data[:300])
print("emp pages ok")

H = {}
def api(path, data):
    t = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/dashboard").get_data(as_text=True)).group(1)
    return c.post(path, json=data, headers={"X-CSRFToken": t})
# geo outside
r = api("/api/geo-check", {"lat":13.5,"lng":77.5,"accuracy":10}).get_json(); assert r["ok"] and not r["allowed"], r
r = api("/api/geo-check", {"lat":12.9716,"lng":77.5946,"accuracy":500}).get_json(); assert not r["ok"]
r = api("/api/geo-check", {"lat":12.9717,"lng":77.5947,"accuracy":10}).get_json(); assert r["allowed"], r
print("geo ok", r["distance"])
def punch(kind, lat=12.9717, lng=77.5947, frames=None):
    ch = api("/api/challenge", {}).get_json(); assert ch["ok"] and len(ch["prompts"])==2
    frames = frames or [img(1), img(2), img(3)]
    return api("/api/punch", {"kind":kind,"token":ch["token"],"frames":frames,"selfie":img(9,(480,360)),"lat":lat,"lng":lng,"accuracy":12})
# liveness fail (static frames)
r = punch("in", frames=[static_img()]*3); assert r.status_code==400 and "Liveness" in r.get_json()["error"], r.get_json()
# geofence fail
r = punch("in", lat=13.5); assert "from Main Office" in r.get_json()["error"], r.get_json()
# check-out before in
r = punch("out"); assert "not checked in" in r.get_json()["error"]
# token reuse
ch = api("/api/challenge", {}).get_json()
body = {"kind":"in","token":ch["token"],"frames":[img(1),img(2),img(3)],"selfie":img(9,(480,360)),"lat":12.9717,"lng":77.5947,"accuracy":12}
r1 = api("/api/punch", body); assert r1.get_json()["ok"], r1.get_json()
r2 = api("/api/punch", body); assert not r2.get_json()["ok"]
print("punch in ok:", r1.get_json())
# duplicate in
assert "already" in punch("in").get_json()["error"]
# make check-in 9.5h earlier to test OT
with app.app_context():
    from app.db import get_db
    import sqlite3
con = sqlite3.connect(os.environ["DATABASE"])
from datetime import datetime, timedelta
con.execute("UPDATE attendance SET check_in=?", ((datetime.now()-timedelta(hours=9, minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),)); con.commit()
r = punch("out").get_json(); print("out:", r); assert r["ok"] and r["status"]=="OT", r
# selfie access: owner ok, other emp forbidden
row = con.execute("SELECT in_selfie FROM attendance").fetchone()[0]
assert c.get("/api/selfie/"+row).status_code==200
login("E002","emp123"); assert c.get("/api/selfie/"+row).status_code==403
print("selfie acl ok")

# --- leave & OD flow
login("E003","emp123"); t = csrf_from("/leave")
r = c.post("/leave", data={"action":"apply","type_id":1,"from_date":str(datetime.now().date()+timedelta(days=1)),"to_date":str(datetime.now().date()+timedelta(days=3)),"reason":"family","csrf_token":t}, follow_redirects=True)
assert b"Leave request sent" in r.data, r.data.decode()[:2000]
r = c.post("/leave", data={"action":"apply","type_id":1,"from_date":str(datetime.now().date()+timedelta(days=1)),"to_date":str(datetime.now().date()+timedelta(days=1)),"reason":"x","csrf_token":t}, follow_redirects=True)
assert b"overlapping" in r.data
t = csrf_from("/od")
r = c.post("/od", data={"action":"apply","od_date":str(datetime.now().date()),"location":"Client X","purpose":"demo","csrf_token":t}, follow_redirects=True); assert b"OD request sent" in r.data
# HR approves
login("HR001","Admin@1234"); r = c.get("/hr/approvals"); t = tok(r.get_data(as_text=True))
ids = re.findall(r'/hr/approvals/(leave|od)/(\d+)', r.get_data(as_text=True))
for kind, rid in sorted(set(ids)):
    r = c.post(f"/hr/approvals/{kind}/{rid}?tab=pending", data={"decision":"Approved","remarks":"ok","csrf_token":t}); assert r.status_code==302
# OD bypass for E003 far from office
login("E003","emp123")
r = api("/api/geo-check", {"lat":13.5,"lng":77.5,"accuracy":10}).get_json(); assert r["allowed"] and r["od"], r
r = punch("in", lat=13.5, lng=77.5).get_json(); assert r["ok"], r
print("leave/od/approval + OD geofence bypass ok")

# --- HR correction + reports + export
login("HR001","Admin@1234"); r = c.get("/hr/corrections"); t = tok(r.get_data(as_text=True))
y = (datetime.now()-timedelta(days=1)).date()
r = c.post("/hr/corrections", data={"emp_id":2,"date":str(y),"check_in":"09:00","check_out":"14:00","reason":"missed punch","csrf_token":t}, follow_redirects=True)
assert b"New status: HD" in r.data, r.data.decode()[-1500:]
r = c.post("/hr/corrections", data={"emp_id":2,"date":str(y),"check_in":"09:00","check_out":"18:00","reason":"","csrf_token":t}, follow_redirects=True); assert b"mandatory" in r.data
r = c.get("/hr/audit"); assert b"CORRECTION" in r.data and b"CHECK_IN" in r.data
r = c.get("/hr/reports"); assert r.status_code==200
r = c.get("/hr/reports/export"); assert r.status_code==200 and r.data[:2]==b"PK"
sample_xlsx = os.path.join(tmp, "sample.xlsx")
open(sample_xlsx, "wb").write(r.data)
import openpyxl; wb = openpyxl.load_workbook(sample_xlsx); print(wb.sheetnames, wb["Summary"].max_row)
r = c.get("/hr/attendance"); assert b"Shift 1" in r.data and b"Shift 2" in r.data
# employee create
t = csrf_from("/hr/employees/new")
r = c.post("/hr/employees/new", data={"emp_code":"E100","name":"New Hire","shift_id":1,"office_id":1,"role":"employee","active":"on","csrf_token":t}, follow_redirects=True); assert b"Employee created" in r.data
# settings
t = csrf_from("/hr/settings")
r = c.post("/hr/settings", data={"action":"holiday","date":"2026-11-08","name":"Diwali","csrf_token":t}, follow_redirects=True); assert b"Saved" in r.data
print("ALL TESTS PASSED")

# --- user management: revoke / grant / delete
login("HR001","Admin@1234"); r = c.get("/hr/employees"); t = tok(r.get_data(as_text=True))
eid = con.execute("SELECT id FROM employees WHERE emp_code='E100'").fetchone()[0]
c.post(f"/hr/employees/{eid}/access", data={"csrf_token":t}); assert con.execute("SELECT active FROM employees WHERE id=?", (eid,)).fetchone()[0]==0
c.post(f"/hr/employees/{eid}/access", data={"csrf_token":t}); assert con.execute("SELECT active FROM employees WHERE id=?", (eid,)).fetchone()[0]==1
r = c.post(f"/hr/employees/{eid}/delete", data={"csrf_token":t,"confirm":"WRONG"}, follow_redirects=True); assert b"exactly" in r.data
c.post(f"/hr/employees/{eid}/delete", data={"csrf_token":t,"confirm":"E100"})
assert con.execute("SELECT COUNT(*) FROM employees WHERE emp_code='E100'").fetchone()[0]==0
r = c.post("/hr/employees/1/delete", data={"csrf_token":t,"confirm":"HR001"}, follow_redirects=True); assert b"own account" in r.data
# maps link text -> office
t = csrf_from("/hr/settings")
r = c.post("/hr/settings", data={"action":"office_link","link":"https://www.google.com/maps/@11.0168,76.9558,17z","id":"1","csrf_token":t}, follow_redirects=True); assert b"11.016800" in r.data
print("USER MGMT + MAPS LINK OK")


def test_e2e_suite():
    """Pytest hook ensuring test suite discovery and reporting."""
    pass

