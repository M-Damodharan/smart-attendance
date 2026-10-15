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

CREATE TABLE IF NOT EXISTS employee_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL UNIQUE,
    work_hours REAL NOT NULL DEFAULT 10.0,
    overtime_hours REAL NOT NULL DEFAULT 0.0,
    half_day_min_hours REAL NOT NULL DEFAULT 4.0,
    half_day_time TEXT NOT NULL DEFAULT '14:00',
    early_full_day_cutoff TEXT NOT NULL DEFAULT '16:00',
    full_day_time TEXT NOT NULL DEFAULT '20:00',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS weekly_shift_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    shift_id INTEGER NOT NULL REFERENCES shifts(id),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
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
    category_id INTEGER REFERENCES employee_categories(id),
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


def _seed_categories(con):
    """Seed standard employee categories if not present."""
    default_cats = [
        ("Staff", "STAFF", 10.0, 0.0, 4.0, "14:00", "16:00", "20:00"),
        ("Employees", "EMPLOYEES", 12.0, 0.0, 4.0, "14:00", "16:00", "20:00"),
        ("Contract Employees", "CONTRACT", 10.0, 2.0, 4.0, "14:00", "16:00", "20:00"),
    ]
    for name, code, work_h, ot_h, half_h, half_t, cutoff_full, full_t in default_cats:
        con.execute(
            "INSERT OR IGNORE INTO employee_categories (name, code, work_hours, overtime_hours, half_day_min_hours, half_day_time, early_full_day_cutoff, full_day_time) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, code, work_h, ot_h, half_h, half_t, cutoff_full, full_t),
        )


def init_db(app):
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    os.makedirs(app.config["SELFIE_DIR"], exist_ok=True)
    con = sqlite3.connect(app.config["DATABASE"])
    con.execute("PRAGMA journal_mode = WAL")          # concurrent readers while writing
    con.executescript(SCHEMA)

    # Migrations for existing DB instances
    emp_cols = [c[1] for c in con.execute("PRAGMA table_info(employees)").fetchall()]
    if "category_id" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN category_id INTEGER REFERENCES employee_categories(id)")

    _seed_categories(con)

    # Assign default category_id for legacy employee records
    staff_id = con.execute("SELECT id FROM employee_categories WHERE code='STAFF'").fetchone()[0]
    emp_cat_id = con.execute("SELECT id FROM employee_categories WHERE code='EMPLOYEES'").fetchone()[0]
    con.execute("UPDATE employees SET category_id=? WHERE role='hr' AND (category_id IS NULL OR category_id=0)", (staff_id,))
    con.execute("UPDATE employees SET category_id=? WHERE (category_id IS NULL OR category_id=0)", (emp_cat_id,))

    # Update Shift 1 to 08:00 to 20:00 if it was still legacy 09:00
    con.execute("UPDATE shifts SET start_time='08:00', end_time='20:00' WHERE name='Shift 1' AND start_time='09:00'")

    if con.execute("SELECT COUNT(*) FROM shifts").fetchone()[0] == 0:
        _seed_base(con)
    if app.config["DEMO_DATA"] and con.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
        _seed_demo(con)
    else:
        code = (os.environ.get("ADMIN_CODE") or "HR001").strip().upper()
        pw = os.environ.get("ADMIN_PASSWORD") or "damo12345"
        if con.execute("SELECT COUNT(*) FROM employees WHERE role='hr'").fetchone()[0] == 0:
            create_admin(con, code, os.environ.get("ADMIN_NAME", "HR Admin"), pw)
            con.execute("UPDATE employees SET must_change_pw=0 WHERE emp_code=?", (code,))
            print(f"[setup] HR admin {code} created with active login.")
        else:
            con.execute("UPDATE employees SET password_hash=?, must_change_pw=0, active=1 WHERE role='hr' AND emp_code=?",
                        (generate_password_hash(pw), code))
        if con.execute("SELECT COUNT(*) FROM employees WHERE role='employee'").fetchone()[0] == 0:
            emp_pw = generate_password_hash("123456789")
            con.execute(
                "INSERT INTO employees (emp_code, name, email, department, designation, role, shift_id, office_id, category_id, password_hash, must_change_pw, joined_on) "
                "VALUES ('EMP001', 'Employee Staff', 'staff@sribalajigears.com', 'Production', 'Operator', 'employee', "
                "(SELECT MIN(id) FROM shifts), (SELECT MIN(id) FROM offices), ?, ?, 0, date('now'))",
                (emp_cat_id, emp_pw))
            print("[setup] Baseline employee EMP001 created with active login.")
    con.commit()
    con.close()


def create_admin(con, code, name, password):
    staff_id = con.execute("SELECT id FROM employee_categories WHERE code='STAFF'").fetchone()
    cat_id = staff_id[0] if staff_id else None
    con.execute(
        "INSERT INTO employees (emp_code, name, department, designation, role, shift_id, office_id, category_id, password_hash, must_change_pw) "
        "VALUES (?,?,?,?, 'hr', (SELECT MIN(id) FROM shifts), (SELECT MIN(id) FROM offices), ?, ?, 1)",
        (code.strip().upper(), name, "Human Resources", "HR", cat_id, generate_password_hash(password)))


def _seed_base(con):
    """Minimum config every install needs. HR edits these in Settings."""
    con.execute("INSERT INTO offices (name, lat, lng, radius_m) VALUES ('Main Office', 0, 0, 150)")  # 0,0 = NOT SET YET
    con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, full_hours, half_hours, ot_min_hours) VALUES ('Shift 1', '08:00', '20:00', 10, 8.0, 4.0, 1.0)")
    con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, full_hours, half_hours, ot_min_hours) VALUES ('Shift 2', '20:00', '08:00', 10, 8.0, 4.0, 1.0)")
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
    emp_cat_id = con.execute("SELECT id FROM employee_categories WHERE code='EMPLOYEES'").fetchone()[0]
    contract_cat_id = con.execute("SELECT id FROM employee_categories WHERE code='CONTRACT'").fetchone()[0]
    staff_cat_id = con.execute("SELECT id FROM employee_categories WHERE code='STAFF'").fetchone()[0]
    for code, name, dept, desig, shift, cat in (
        ("E001", "Aarav Sharma", "Engineering", "Developer", 1, staff_cat_id),
        ("E002", "Meera Iyer", "Engineering", "QA Engineer", 1, emp_cat_id),
        ("E003", "Rohan Das", "Support", "Support Executive", 2, emp_cat_id),
        ("E004", "Sana Khan", "Support", "Team Lead", 2, contract_cat_id),
    ):
        con.execute(
            "INSERT INTO employees (emp_code, name, email, department, designation, role, shift_id, office_id, category_id, password_hash, must_change_pw, joined_on) "
            "VALUES (?,?,?,?,?, 'employee', ?, 1, ?, ?, 0, '2024-01-01')",
            (code, name, f"{code.lower()}@example.com", dept, desig, shift, cat, emp_pw))
