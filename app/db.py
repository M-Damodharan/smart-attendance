import os
import sqlite3
from datetime import date

from flask import current_app, g
from werkzeug.security import generate_password_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS offices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    radius_m INTEGER NOT NULL DEFAULT 150,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    start_time TEXT NOT NULL,          -- HH:MM
    end_time TEXT NOT NULL,            -- HH:MM
    grace_min INTEGER NOT NULL DEFAULT 10,
    full_hours REAL NOT NULL DEFAULT 8,     -- >= this  => Present
    half_hours REAL NOT NULL DEFAULT 4,     -- >= this  => Half Day, below => Absent
    ot_min_hours REAL NOT NULL DEFAULT 1,   -- worked >= full + this => OT
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    department TEXT,
    designation TEXT,
    role TEXT NOT NULL DEFAULT 'employee' CHECK (role IN ('employee','hr')),
    shift_id INTEGER REFERENCES shifts(id),
    office_id INTEGER REFERENCES offices(id),
    password_hash TEXT NOT NULL,
    must_change_pw INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    joined_on TEXT NOT NULL DEFAULT (date('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    date TEXT NOT NULL,
    check_in TEXT,
    check_out TEXT,
    in_lat REAL, in_lng REAL, in_dist REAL, in_selfie TEXT, in_geo_ok INTEGER,
    out_lat REAL, out_lng REAL, out_dist REAL, out_selfie TEXT, out_geo_ok INTEGER,
    work_hours REAL NOT NULL DEFAULT 0,
    ot_hours REAL NOT NULL DEFAULT 0,
    status TEXT,                        -- P / HD / A / OT (set at check-out)
    late INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'app', -- app | correction
    remarks TEXT,
    UNIQUE (emp_id, date)
);

CREATE TABLE IF NOT EXISTS leave_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    annual_quota REAL NOT NULL DEFAULT 12
);

CREATE TABLE IF NOT EXISTS leaves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    type_id INTEGER NOT NULL REFERENCES leave_types(id),
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    days REAL NOT NULL,
    half_day INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'Pending',   -- Pending / Approved / Rejected / Cancelled
    decided_by INTEGER REFERENCES employees(id),
    decided_at TEXT,
    hr_remarks TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS od_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    od_date TEXT NOT NULL,
    from_time TEXT,
    to_time TEXT,
    location TEXT NOT NULL,
    purpose TEXT,
    status TEXT NOT NULL DEFAULT 'Pending',
    decided_by INTEGER REFERENCES employees(id),
    decided_at TEXT,
    hr_remarks TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS holidays (
    date TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    actor_id INTEGER,
    actor_name TEXT,
    action TEXT NOT NULL,
    entity TEXT,
    entity_id TEXT,
    details TEXT,
    ip TEXT
);

CREATE TABLE IF NOT EXISTS challenges (
    token TEXT PRIMARY KEY,
    emp_id INTEGER NOT NULL,
    prompts TEXT NOT NULL,
    created_at REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS login_attempts (
    key TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    first_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(date);
CREATE INDEX IF NOT EXISTS idx_att_emp ON attendance(emp_id, date);
CREATE INDEX IF NOT EXISTS idx_leave_emp ON leaves(emp_id, status);
CREATE INDEX IF NOT EXISTS idx_od_emp ON od_requests(emp_id, od_date);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 8000")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    os.makedirs(app.config["SELFIE_DIR"], exist_ok=True)
    con = sqlite3.connect(app.config["DATABASE"])
    con.execute("PRAGMA journal_mode = WAL")          # concurrent readers while writing
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM shifts").fetchone()[0] == 0:
        _seed_base(con)
    if app.config["DEMO_DATA"] and con.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
        _seed_demo(con)
    elif con.execute("SELECT COUNT(*) FROM employees WHERE role='hr'").fetchone()[0] == 0:
        code, pw = os.environ.get("ADMIN_CODE"), os.environ.get("ADMIN_PASSWORD")
        if code and pw:
            create_admin(con, code, os.environ.get("ADMIN_NAME", "HR Admin"), pw)
            print(f"[setup] HR admin {code.upper()} created (password change required at first login)")
        else:
            print("[setup] No HR user yet. Run:  python manage.py create-admin")
    con.commit()
    con.close()


def create_admin(con, code, name, password):
    con.execute(
        "INSERT INTO employees (emp_code, name, department, designation, role, shift_id, office_id, password_hash, must_change_pw) "
        "VALUES (?,?,?,?, 'hr', (SELECT MIN(id) FROM shifts), (SELECT MIN(id) FROM offices), ?, 1)",
        (code.strip().upper(), name, "Human Resources", "HR", generate_password_hash(password)))


def _seed_base(con):
    """Minimum config every install needs. HR edits these in Settings."""
    con.execute("INSERT INTO offices (name, lat, lng, radius_m) VALUES ('Main Office', 0, 0, 150)")  # 0,0 = NOT SET YET
    con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min) VALUES ('Shift 1', '09:00', '18:00', 10)")
    con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min) VALUES ('Shift 2', '13:00', '22:00', 10)")
    for code, name, q in (("CL", "Casual Leave", 12), ("SL", "Sick Leave", 12), ("PL", "Privilege Leave", 15)):
        con.execute("INSERT INTO leave_types (code, name, annual_quota) VALUES (?,?,?)", (code, name, q))
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '350')")
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '50')")


def _seed_demo(con):
    """Demo data for development only (DEMO_DATA=1)."""
    con.execute("UPDATE offices SET lat=12.9716, lng=77.5946, radius_m=200 WHERE id=1")
    for d, n in (("2026-10-02", "Gandhi Jayanti"), ("2026-12-25", "Christmas"), ("2027-01-26", "Republic Day")):
        con.execute("INSERT OR IGNORE INTO holidays (date, name) VALUES (?,?)", (d, n))
    create_admin(con, "HR001", "HR Admin", "admin123")
    con.execute("UPDATE employees SET joined_on='2024-01-01' WHERE emp_code='HR001'")
    emp_pw = generate_password_hash("emp123")
    for code, name, dept, desig, shift in (("E001", "Aarav Sharma", "Engineering", "Developer", 1),
                                           ("E002", "Meera Iyer", "Engineering", "QA Engineer", 1),
                                           ("E003", "Rohan Das", "Support", "Support Executive", 2),
                                           ("E004", "Sana Khan", "Support", "Team Lead", 2)):
        con.execute(
            "INSERT INTO employees (emp_code, name, email, department, designation, role, shift_id, office_id, password_hash, must_change_pw, joined_on) "
            "VALUES (?,?,?,?,?, 'employee', ?, 1, ?, 0, '2024-01-01')",
            (code, name, f"{code.lower()}@example.com", dept, desig, shift, emp_pw))
