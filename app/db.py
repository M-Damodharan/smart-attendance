import os
import sqlite3
from datetime import date

from flask import current_app, g
from werkzeug.security import generate_password_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS offices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department TEXT,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    radius_m INTEGER NOT NULL DEFAULT 50,
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
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE TABLE IF NOT EXISTS weekly_shift_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    shift_id INTEGER NOT NULL REFERENCES shifts(id),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    emergency_contact TEXT,
    department TEXT,
    designation TEXT,
    role TEXT NOT NULL DEFAULT 'employee' CHECK (role IN ('employee','hr')),
    shift_id INTEGER REFERENCES shifts(id),
    office_id INTEGER REFERENCES offices(id),
    category_id INTEGER REFERENCES employee_categories(id),
    password_hash TEXT NOT NULL,
    must_change_pw INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    block_reason TEXT,
    blocked_at TEXT,
    device_registered INTEGER NOT NULL DEFAULT 0,
    device_name TEXT,
    device_registered_at TEXT,
    face_registered INTEGER NOT NULL DEFAULT 0,
    face_template TEXT,
    face_registered_at TEXT,
    face_registered_by INTEGER REFERENCES employees(id),
    face_image TEXT,
    joined_on TEXT NOT NULL DEFAULT (date('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    shift_id INTEGER REFERENCES shifts(id),
    date TEXT NOT NULL,
    check_in TEXT,
    check_out TEXT,
    in_lat REAL, in_lng REAL, in_dist REAL, in_selfie TEXT, in_geo_ok INTEGER,
    out_lat REAL, out_lng REAL, out_dist REAL, out_selfie TEXT, out_geo_ok INTEGER,
    work_hours REAL NOT NULL DEFAULT 0,
    ot_hours REAL NOT NULL DEFAULT 0,
    status TEXT,                        -- P / HD / A / OT (set at check-out)
    late INTEGER NOT NULL DEFAULT 0,
    face_verified INTEGER NOT NULL DEFAULT 0,
    face_similarity REAL NOT NULL DEFAULT 0.0,
    source TEXT NOT NULL DEFAULT 'app', -- app | correction
    auth_method TEXT DEFAULT 'webauthn_face_id',
    device_credential_id TEXT,
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
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
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
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE TABLE IF NOT EXISTS permission_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    perm_date TEXT NOT NULL,
    perm_type TEXT NOT NULL DEFAULT 'Late Coming', -- Late Coming / Early Leaving / Official / Bank Work
    duration_hours REAL NOT NULL DEFAULT 2.0,       -- 1, 2, 3, 4 hours
    from_time TEXT,
    to_time TEXT,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending',        -- Pending / Approved / Rejected / Cancelled
    decided_by INTEGER REFERENCES employees(id),
    decided_at TEXT,
    hr_remarks TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE TABLE IF NOT EXISTS face_change_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending',   -- Pending / Approved / Rejected / Completed
    requested_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
    decided_by INTEGER REFERENCES employees(id),
    decided_at TEXT,
    hr_remarks TEXT
);

CREATE INDEX IF NOT EXISTS idx_face_change_emp ON face_change_requests(emp_id, status);

CREATE TABLE IF NOT EXISTS holidays (
    date TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
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

CREATE TABLE IF NOT EXISTS ai_face_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    target_emp_id INTEGER REFERENCES employees(id),
    similarity REAL NOT NULL DEFAULT 0.0,
    selfie_path TEXT,
    target_selfie_path TEXT,
    alert_type TEXT NOT NULL DEFAULT 'BUDDY_PUNCH',
    status TEXT NOT NULL DEFAULT 'BLOCKED',
    details TEXT,
    hr_action_by INTEGER REFERENCES employees(id),
    hr_action_at TEXT,
    hr_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    credential_id TEXT NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    transports TEXT,
    device_name TEXT,
    aaguid TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(date);
CREATE INDEX IF NOT EXISTS idx_att_emp ON attendance(emp_id, date);
CREATE INDEX IF NOT EXISTS idx_attendance_shift ON attendance(shift_id);
CREATE INDEX IF NOT EXISTS idx_leave_emp ON leaves(emp_id, status);
CREATE INDEX IF NOT EXISTS idx_leaves_dates ON leaves(emp_id, from_date, to_date);
CREATE INDEX IF NOT EXISTS idx_leaves_status ON leaves(status);
CREATE INDEX IF NOT EXISTS idx_od_emp ON od_requests(emp_id, od_date);
CREATE INDEX IF NOT EXISTS idx_od_status ON od_requests(status);
CREATE INDEX IF NOT EXISTS idx_perm_emp ON permission_requests(emp_id, perm_date);
CREATE INDEX IF NOT EXISTS idx_perm_status ON permission_requests(status);
CREATE INDEX IF NOT EXISTS idx_ai_alert_date ON ai_face_alerts(date);
CREATE INDEX IF NOT EXISTS idx_ai_alert_emp ON ai_face_alerts(emp_id);
CREATE INDEX IF NOT EXISTS idx_webauthn_emp ON webauthn_credentials(emp_id);
CREATE INDEX IF NOT EXISTS idx_webauthn_cred ON webauthn_credentials(credential_id);
CREATE INDEX IF NOT EXISTS idx_employees_active_role ON employees(active, role);
CREATE INDEX IF NOT EXISTS idx_employees_code ON employees(emp_code);
CREATE INDEX IF NOT EXISTS idx_holidays_date ON holidays(date);
CREATE INDEX IF NOT EXISTS idx_shifts_active ON shifts(active);
CREATE INDEX IF NOT EXISTS idx_face_change_emp ON face_change_requests(emp_id, status);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL DEFAULT 'attendance',
    entity_id INTEGER NOT NULL,
    emp_id INTEGER NOT NULL REFERENCES employees(id),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE INDEX IF NOT EXISTS idx_comments_entity ON comments(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_comments_emp ON comments(emp_id);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES employees(id),
    target_role TEXT,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'info',
    status TEXT NOT NULL DEFAULT 'unread',
    link_url TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE TABLE IF NOT EXISTS durable_photos (
    filename TEXT PRIMARY KEY,
    emp_code TEXT,
    photo_data TEXT,
    mime_type TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
);

CREATE INDEX IF NOT EXISTS idx_durable_photos_emp ON durable_photos(emp_code);
"""


def get_db():
    if "db" not in g:
        use_turso = current_app.config.get("USE_TURSO") and not current_app.config.get("TESTING")
        if use_turso:
            from app.turso_db import connect_turso
            g.db = connect_turso(
                current_app.config["TURSO_DATABASE_URL"],
                current_app.config["TURSO_AUTH_TOKEN"]
            )
        else:
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
        ("Staff", "STAFF", 10.0, 0.0, 2.0, "14:00", "16:00", "20:00"),
        ("Employees", "EMPLOYEES", 12.0, 0.0, 2.0, "14:00", "16:00", "20:00"),
        ("Contract Employees", "CONTRACT", 10.0, 2.0, 2.0, "14:00", "16:00", "20:00"),
        ("Accounts & Finance", "ACCOUNTS_FINANCE", 10.0, 0.0, 2.0, "14:00", "16:00", "18:00"),
        ("Late Come Permission", "LATE_PERMISSION", 10.0, 0.0, 2.0, "14:00", "16:00", "20:00"),
    ]
    for name, code, work_h, ot_h, half_h, half_t, cutoff_full, full_t in default_cats:
        con.execute(
            "INSERT OR IGNORE INTO employee_categories (name, code, work_hours, overtime_hours, half_day_min_hours, half_day_time, early_full_day_cutoff, full_day_time) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, code, work_h, ot_h, half_h, half_t, cutoff_full, full_t),
        )


def init_db(app):
    db_file = app.config["DATABASE"]
    selfie_dir = app.config["SELFIE_DIR"]
    os.makedirs(os.path.dirname(db_file), exist_ok=True)
    os.makedirs(selfie_dir, exist_ok=True)

    # Automatically restore from latest backup or persistent seed data if database does not exist or is empty
    from config import BASE_DIR
    import shutil
    latest_backup_db = os.path.join(BASE_DIR, "backups", "latest", "attendance.db")
    seed_db = os.path.join(BASE_DIR, "app", "seed_data", "attendance.db")

    is_test_env = app.config.get("TESTING") or app.config.get("DEMO_DATA")
    use_turso = bool(app.config.get("USE_TURSO")) and not is_test_env

    # CRITICAL: In production using 24/7 Cloud Database, NEVER overwrite data with old seed files!
    should_auto_restore = ((not is_test_env) or app.config.get("FORCE_RECOVERY_TEST")) and (not use_turso)

    if (not os.path.exists(db_file) or os.path.getsize(db_file) == 0) and should_auto_restore:
        if os.path.exists(latest_backup_db) and os.path.getsize(latest_backup_db) > 0:
            try:
                shutil.copy2(latest_backup_db, db_file)
                print(f"[setup] Restored database from latest backup at {latest_backup_db}")
            except Exception as e:
                print(f"[setup] Warning: could not restore latest backup: {e}")
        elif os.path.exists(seed_db) and os.path.getsize(seed_db) > 0:
            try:
                shutil.copy2(seed_db, db_file)
                print(f"[setup] Restored database from seed data at {seed_db}")
            except Exception as e:
                print(f"[setup] Warning: could not restore seed database: {e}")

    # Copy seed/backup selfies if missing in target selfie_dir
    latest_selfies = os.path.join(BASE_DIR, "backups", "latest", "selfies")
    seed_selfies = os.path.join(BASE_DIR, "app", "seed_data", "selfies")
    for s_dir in (latest_selfies, seed_selfies):
        if os.path.exists(s_dir):
            try:
                for fname in os.listdir(s_dir):
                    src_f = os.path.join(s_dir, fname)
                    dst_f = os.path.join(selfie_dir, fname)
                    if os.path.isfile(src_f) and not os.path.exists(dst_f):
                        shutil.copy2(src_f, dst_f)
            except Exception as e:
                print(f"[setup] Warning: could not restore selfies from {s_dir}: {e}")

    if use_turso:
        from app.turso_db import connect_turso
        con = connect_turso(app.config["TURSO_DATABASE_URL"], app.config["TURSO_AUTH_TOKEN"])
        print("[setup] Active 24/7 Turso Cloud Database connected in AWS Mumbai.")
    else:
        con = sqlite3.connect(db_file)
        con.execute("PRAGMA journal_mode = WAL")          # concurrent readers while writing

    con.executescript(SCHEMA)


    # Migrations for existing DB instances
    emp_cols = [c[1] for c in con.execute("PRAGMA table_info(employees)").fetchall()]
    if "category_id" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN category_id INTEGER REFERENCES employee_categories(id)")
    if "face_registered" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN face_registered INTEGER NOT NULL DEFAULT 0")
    if "face_template" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN face_template TEXT")
    if "face_registered_at" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN face_registered_at TEXT")
    if "face_registered_by" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN face_registered_by INTEGER REFERENCES employees(id)")
    if "face_image" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN face_image TEXT")
    if "block_reason" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN block_reason TEXT")
    if "blocked_at" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN blocked_at TEXT")
    if "device_registered" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN device_registered INTEGER NOT NULL DEFAULT 0")
    if "device_name" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN device_name TEXT")
    if "device_registered_at" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN device_registered_at TEXT")
    if "emergency_contact" not in emp_cols:
        con.execute("ALTER TABLE employees ADD COLUMN emergency_contact TEXT")

    att_cols = [c[1] for c in con.execute("PRAGMA table_info(attendance)").fetchall()]
    if "shift_id" not in att_cols:
        con.execute("ALTER TABLE attendance ADD COLUMN shift_id INTEGER REFERENCES shifts(id)")
    if "face_verified" not in att_cols:
        con.execute("ALTER TABLE attendance ADD COLUMN face_verified INTEGER NOT NULL DEFAULT 0")
    if "face_similarity" not in att_cols:
        con.execute("ALTER TABLE attendance ADD COLUMN face_similarity REAL NOT NULL DEFAULT 0.0")
    if "auth_method" not in att_cols:
        con.execute("ALTER TABLE attendance ADD COLUMN auth_method TEXT DEFAULT 'webauthn_face_id'")
    if "device_credential_id" not in att_cols:
        con.execute("ALTER TABLE attendance ADD COLUMN device_credential_id TEXT")

    office_cols = [c[1] for c in con.execute("PRAGMA table_info(offices)").fetchall()]
    if "department" not in office_cols:
        con.execute("ALTER TABLE offices ADD COLUMN department TEXT")

    # Strict 50 m location geofence policy: radius_m = 50 max, geofence_tolerance_m = 0
    con.execute("UPDATE offices SET radius_m=50 WHERE radius_m > 50")
    con.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")

    # Create permission_requests table if missing in existing database
    con.execute("""
    CREATE TABLE IF NOT EXISTS permission_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id INTEGER NOT NULL REFERENCES employees(id),
        perm_date TEXT NOT NULL,
        perm_type TEXT NOT NULL DEFAULT 'Late Coming',
        duration_hours REAL NOT NULL DEFAULT 2.0,
        from_time TEXT,
        to_time TEXT,
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending',
        decided_by INTEGER REFERENCES employees(id),
        decided_at TEXT,
        hr_remarks TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
    )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_perm_emp ON permission_requests(emp_id, perm_date)")

    _seed_categories(con)

    # Ensure Accounts & Finance and Late Come Permission exist
    con.execute("INSERT OR IGNORE INTO employee_categories (name, code, work_hours, overtime_hours, half_day_min_hours, half_day_time, early_full_day_cutoff, full_day_time) "
                "VALUES ('Accounts & Finance', 'ACCOUNTS_FINANCE', 10.0, 0.0, 2.0, '14:00', '16:00', '18:00')")
    con.execute("INSERT OR IGNORE INTO employee_categories (name, code, work_hours, overtime_hours, half_day_min_hours, half_day_time, early_full_day_cutoff, full_day_time) "
                "VALUES ('Late Come Permission', 'LATE_PERMISSION', 10.0, 0.0, 2.0, '14:00', '16:00', '20:00')")
    con.execute("UPDATE employee_categories SET code='ACCOUNTS_FINANCE' WHERE name LIKE '%Accounts%' AND (code IS NULL OR code='' OR code='ACCOUNTS')")
    con.execute("UPDATE employee_categories SET half_day_min_hours=2.0 WHERE half_day_min_hours > 2.0")

    # Clean up any leftover test dummy employees and orphaned records
    con.execute("DELETE FROM employees WHERE emp_code IN ('AUDITEMP01', 'LOCALTEST01', 'TESTGEN01', 'TEST001', 'TEST002')")
    con.execute("DELETE FROM attendance WHERE emp_id NOT IN (SELECT id FROM employees)")
    con.execute("DELETE FROM audit_log WHERE entity_id NOT IN (SELECT id FROM employees) AND entity='employee'")

    # Ensure Permission leave type exists
    con.execute("INSERT OR IGNORE INTO leave_types (code, name, annual_quota) VALUES ('PERM', 'Permission (Late Come / Early Go)', 24)")

    # Ensure Accounts & Finance shift exists
    if con.execute("SELECT COUNT(*) FROM shifts WHERE name LIKE '%Accounts%'").fetchone()[0] == 0:
        con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, full_hours, half_hours, ot_min_hours, active) "
                    "VALUES ('Accounts & Finance (09:00-18:00)', '09:00', '18:00', 15, 8.0, 4.0, 1.0, 1)")

    # Assign default category_id for legacy employee records
    staff_id = con.execute("SELECT id FROM employee_categories WHERE code='STAFF'").fetchone()[0]
    emp_cat_id = con.execute("SELECT id FROM employee_categories WHERE code='EMPLOYEES'").fetchone()[0]
    con.execute("UPDATE employees SET category_id=? WHERE role='hr' AND (category_id IS NULL OR category_id=0)", (staff_id,))
    con.execute("UPDATE employees SET category_id=? WHERE (category_id IS NULL OR category_id=0)", (emp_cat_id,))
    if con.execute("SELECT COUNT(*) FROM offices").fetchone()[0] == 0:
        _seed_base(con)

    # Seed & Ensure 4 Department-Wise Marked Location Points exist (strict 50 m radius):
    # 1. Staff (Staff, Engineering, Admin, HR)
    # 2. Employees (Production, Machine Shop, Operators)
    # 3. Contract Labour (Contract Operations, Logistics, Loading)
    # 4. Accounts & Finance
    base_off = con.execute("SELECT lat, lng FROM offices WHERE lat != 0 ORDER BY id LIMIT 1").fetchone()
    def_lat = base_off[0] if base_off else 12.9716
    def_lng = base_off[1] if base_off else 77.5946

    con.execute("UPDATE offices SET department='Staff', radius_m=50 WHERE id=1 AND (department IS NULL OR department='')")

    dept_points = [
        ("Staff - Engineering & Admin Office", "Staff", def_lat, def_lng),
        ("Employees - Production & Factory Floor", "Production", def_lat, def_lng),
        ("Contract Labour - Operations & Yard", "Contract Operations", def_lat, def_lng),
        ("Accounts & Finance - Finance Office", "Accounts & Finance", def_lat, def_lng),
    ]
    for name, dept, d_lat, d_lng in dept_points:
        row = con.execute("SELECT id FROM offices WHERE department=? OR name=?", (dept, name)).fetchone()
        if not row:
            con.execute(
                "INSERT INTO offices (name, department, lat, lng, radius_m, active) VALUES (?,?,?,?, 50, 1)",
                (name, dept, d_lat, d_lng)
            )
        else:
            con.execute("UPDATE offices SET department=?, radius_m=50 WHERE id=?", (dept, row[0]))

    # Link employees to their marked department location points:
    staff_off = con.execute("SELECT id FROM offices WHERE department='Staff' ORDER BY id LIMIT 1").fetchone()
    prod_off = con.execute("SELECT id FROM offices WHERE department='Production' ORDER BY id LIMIT 1").fetchone()
    contract_off = con.execute("SELECT id FROM offices WHERE department='Contract Operations' ORDER BY id LIMIT 1").fetchone()
    acc_off = con.execute("SELECT id FROM offices WHERE department='Accounts & Finance' ORDER BY id LIMIT 1").fetchone()

    if staff_off:
        con.execute("UPDATE employees SET office_id=? WHERE (office_id IS NULL OR office_id=1) AND (department IN ('Staff', 'Engineering', 'Human Resources', 'IT') OR role='hr')", (staff_off[0],))
    if prod_off:
        con.execute("UPDATE employees SET office_id=? WHERE (office_id IS NULL OR office_id=1) AND department IN ('Production', 'Employees')", (prod_off[0],))
    if contract_off:
        con.execute("UPDATE employees SET office_id=? WHERE (office_id IS NULL OR office_id=1) AND (department IN ('Contract Operations', 'Contract Labour') OR department LIKE '%Contract%')", (contract_off[0],))
    if acc_off:
        con.execute("UPDATE employees SET office_id=? WHERE (office_id IS NULL OR office_id=1) AND (department IN ('Accounts & Finance', 'Accounts') OR department LIKE '%Accounts%')", (acc_off[0],))

    # Ensure Accounts & Finance shift exists and link accounts employees
    acc_shift = con.execute("SELECT id FROM shifts WHERE (name LIKE '%Accounts%' OR name LIKE '%Finance%') AND active=1 ORDER BY id LIMIT 1").fetchone()
    if not acc_shift:
        con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, full_hours, half_hours, ot_min_hours, active) "
                    "VALUES ('Accounts & Finance (09:00-18:00)', '09:00', '18:00', 15, 8.0, 4.0, 1.0, 1)")
        acc_shift = con.execute("SELECT id FROM shifts WHERE name LIKE '%Accounts%' ORDER BY id LIMIT 1").fetchone()

    acc_cat = con.execute("SELECT id FROM employee_categories WHERE code='ACCOUNTS_FINANCE'").fetchone()
    if acc_cat and acc_shift:
        con.execute("UPDATE employees SET category_id=? WHERE (department LIKE '%Accounts%' OR department LIKE '%Finance%') AND (category_id IS NULL OR category_id NOT IN (SELECT id FROM employee_categories WHERE code='ACCOUNTS_FINANCE'))", (acc_cat[0],))
        con.execute("UPDATE employees SET shift_id=? WHERE (category_id=? OR department LIKE '%Accounts%' OR department LIKE '%Finance%') AND (shift_id=1 OR shift_id IS NULL)", (acc_shift[0], acc_cat[0]))
        con.execute("UPDATE attendance SET shift_id=? WHERE emp_id IN (SELECT id FROM employees WHERE category_id=? OR department LIKE '%Accounts%' OR department LIKE '%Finance%') AND shift_id=1", (acc_shift[0], acc_cat[0]))
        if acc_off:
            con.execute("UPDATE employees SET office_id=? WHERE (category_id=? OR department LIKE '%Accounts%' OR department LIKE '%Finance%') AND (office_id=1 OR office_id IS NULL)", (acc_off[0], acc_cat[0]))

    # Propagate real coordinates if set (e.g. from office with non-default coordinates):
    real_coord_row = con.execute("SELECT lat, lng FROM offices WHERE lat != 0 AND lat != 12.9716 AND active=1 ORDER BY id LIMIT 1").fetchone()
    if real_coord_row:
        real_lat, real_lng = real_coord_row[0], real_coord_row[1]
        con.execute("UPDATE offices SET lat=?, lng=? WHERE (lat=0 OR lat=12.9716) AND active=1", (real_lat, real_lng))

    # Ensure General Shift, Morning Shift, and Evening Shift exist
    con.execute("UPDATE shifts SET name='Shift 1 (Morning Shift)', start_time='08:00', end_time='20:00' WHERE (name LIKE '%Shift 1%' OR id=1)")
    con.execute("UPDATE shifts SET name='Shift 2 (Evening Shift)', start_time='20:00', end_time='08:00' WHERE (name LIKE '%Shift 2%' OR id=2)")
    if con.execute("SELECT COUNT(*) FROM shifts WHERE name LIKE '%General%'").fetchone()[0] == 0:
        con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, full_hours, half_hours, ot_min_hours) VALUES ('General Shift', '09:00', '18:00', 15, 8.0, 4.0, 1.0)")

    # Deduplicate shifts: merge any duplicate Shift 2 rows so only one Shift 2 exists
    s2_rows = con.execute("SELECT id FROM shifts WHERE name LIKE '%Shift 2%' ORDER BY id").fetchall()
    if len(s2_rows) > 1:
        keep_s2 = s2_rows[0][0]
        for dup in s2_rows[1:]:
            dup_id = dup[0]
            con.execute("UPDATE employees SET shift_id=? WHERE shift_id=?", (keep_s2, dup_id))
            con.execute("UPDATE attendance SET shift_id=? WHERE shift_id=?", (keep_s2, dup_id))
            con.execute("UPDATE weekly_shift_schedules SET shift_id=? WHERE shift_id=?", (keep_s2, dup_id))
            con.execute("DELETE FROM shifts WHERE id=?", (dup_id,))

    # Generic deduplication: keep earliest ID for any duplicate shift names
    dup_names = con.execute("SELECT name FROM shifts GROUP BY name HAVING COUNT(*) > 1").fetchall()
    for row in dup_names:
        name = row[0]
        ids = [r[0] for r in con.execute("SELECT id FROM shifts WHERE name=? ORDER BY id", (name,)).fetchall()]
        if len(ids) > 1:
            primary_id = ids[0]
            for extra_id in ids[1:]:
                con.execute("UPDATE employees SET shift_id=? WHERE shift_id=?", (primary_id, extra_id))
                con.execute("UPDATE attendance SET shift_id=? WHERE shift_id=?", (primary_id, extra_id))
                con.execute("UPDATE weekly_shift_schedules SET shift_id=? WHERE shift_id=?", (primary_id, extra_id))
                con.execute("DELETE FROM shifts WHERE id=?", (extra_id,))

    # Ensure system_settings has face recognition and AI defense defaults (calibrated for real-world phone cameras)
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('require_face_match', '1')")
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('face_match_threshold', '0.58')")
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('ai_buddy_punch_block_threshold', '0.60')")
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('ai_auto_block_enabled', '1')")
    con.execute("UPDATE system_settings SET value='0.58' WHERE key='face_match_threshold' AND (value='0.75' OR value='0.70')")
    con.execute("UPDATE system_settings SET value='0.60' WHERE key='ai_buddy_punch_block_threshold'")

    con.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES employees(id),
        target_role TEXT,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'info',
        status TEXT NOT NULL DEFAULT 'unread',
        link_url TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
    );
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, status);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_notif_role ON notifications(target_role, status);")

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

    # In automated test mode ONLY, populate baseline test fixtures for unit tests
    if is_test_env:
        emp_pw = generate_password_hash("123456789")
        contract_cat_id = con.execute("SELECT id FROM employee_categories WHERE code='CONTRACT'").fetchone()[0]
        if con.execute("SELECT COUNT(*) FROM employees WHERE emp_code='EMP001'").fetchone()[0] == 0:
            con.execute(
                "INSERT INTO employees (emp_code, name, email, department, designation, role, shift_id, office_id, category_id, password_hash, must_change_pw, joined_on) "
                "VALUES ('EMP001', 'Employee Staff', 'staff@sribalajigears.com', 'Production', 'Operator', 'employee', "
                "(SELECT MIN(id) FROM shifts), (SELECT MIN(id) FROM offices), ?, ?, 0, date('now'))",
                (emp_cat_id, emp_pw))
        if con.execute("SELECT COUNT(*) FROM employees WHERE emp_code='E0012'").fetchone()[0] == 0:
            con.execute(
                "INSERT INTO employees (emp_code, name, email, department, designation, role, shift_id, office_id, category_id, password_hash, must_change_pw, joined_on) "
                "VALUES ('E0012', 'Damodharan M', 'damodharan@sribalajigears.com', 'Engineering', 'Developer', 'employee', "
                "(SELECT MIN(id) FROM shifts), (SELECT MIN(id) FROM offices), ?, ?, 0, date('now'))",
                (staff_id, emp_pw))
        if con.execute("SELECT COUNT(*) FROM employees WHERE emp_code='CL001'").fetchone()[0] == 0:
            con.execute(
                "INSERT INTO employees (emp_code, name, email, department, designation, role, shift_id, office_id, category_id, password_hash, must_change_pw, joined_on) "
                "VALUES ('CL001', 'Ramesh Kumar', 'ramesh@sribalajigears.com', 'Contract Operations', 'Contract Labour', 'employee', "
                "(SELECT MIN(id) FROM shifts), (SELECT MIN(id) FROM offices), ?, ?, 0, date('now'))",
                (contract_cat_id, emp_pw))
        accounts_cat_row = con.execute("SELECT id FROM employee_categories WHERE code='ACCOUNTS_FINANCE'").fetchone()
        if accounts_cat_row:
            acc_id = accounts_cat_row[0]
            if con.execute("SELECT COUNT(*) FROM employees WHERE emp_code='S197'").fetchone()[0] == 0:
                con.execute(
                    "INSERT INTO employees (emp_code, name, email, department, designation, role, shift_id, office_id, category_id, password_hash, must_change_pw, joined_on) "
                    "VALUES ('S197', 'Accounts Executive', 'accounts@sribalajigears.com', 'Accounts & Finance', 'Accountant', 'employee', "
                    "(SELECT MIN(id) FROM shifts), (SELECT MIN(id) FROM offices), ?, ?, 0, date('now'))",
                    (acc_id, emp_pw))

    con.commit()
    con.close()

    # Automatically take an atomic startup backup so data is preserved across restarts
    if not app.config.get("TESTING"):
        try:
            from app.backup_manager import create_backup
            create_backup(reason="startup")
        except Exception:
            pass


def create_admin(con, code, name, password):
    staff_id = con.execute("SELECT id FROM employee_categories WHERE code='STAFF'").fetchone()
    cat_id = staff_id[0] if staff_id else None
    con.execute(
        "INSERT INTO employees (emp_code, name, department, designation, role, shift_id, office_id, category_id, password_hash, must_change_pw) "
        "VALUES (?,?,?,?, 'hr', (SELECT MIN(id) FROM shifts), (SELECT MIN(id) FROM offices), ?, ?, 1)",
        (code.strip().upper(), name, "Human Resources", "HR", cat_id, generate_password_hash(password)))


def _seed_base(con):
    """Minimum config every install needs. HR edits these in Settings."""
    con.execute("INSERT INTO offices (name, department, lat, lng, radius_m) VALUES ('Staff - Engineering & Admin Office', 'Staff', 0, 0, 50)")  # 0,0 = NOT SET YET
    con.execute("INSERT INTO offices (name, department, lat, lng, radius_m) VALUES ('Employees - Production & Factory Floor', 'Production', 0, 0, 50)")
    con.execute("INSERT INTO offices (name, department, lat, lng, radius_m) VALUES ('Contract Labour - Operations & Yard', 'Contract Operations', 0, 0, 50)")
    con.execute("INSERT INTO offices (name, department, lat, lng, radius_m) VALUES ('Accounts & Finance - Finance Office', 'Accounts & Finance', 0, 0, 50)")
    con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, full_hours, half_hours, ot_min_hours) VALUES ('General Shift', '09:00', '18:00', 15, 8.0, 4.0, 1.0)")
    con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, full_hours, half_hours, ot_min_hours) VALUES ('Shift 1 (Morning Shift)', '08:00', '20:00', 10, 8.0, 4.0, 1.0)")
    con.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, full_hours, half_hours, ot_min_hours) VALUES ('Shift 2 (Evening Shift)', '20:00', '08:00', 10, 8.0, 4.0, 1.0)")
    for code, name, q in (("CL", "Casual Leave", 12), ("SL", "Sick Leave", 12), ("PL", "Privilege Leave", 15)):
        con.execute("INSERT INTO leave_types (code, name, annual_quota) VALUES (?,?,?)", (code, name, q))
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '350')")
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('require_face_match', '1')")
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('face_match_threshold', '0.58')")
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('ai_buddy_punch_block_threshold', '0.60')")
    con.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('ai_auto_block_enabled', '1')")


def _seed_demo(con):
    """Demo data for development only (DEMO_DATA=1)."""
    con.execute("UPDATE offices SET lat=12.9716, lng=77.5946, radius_m=50 WHERE id=1 OR department='Staff'")
    con.execute("UPDATE offices SET lat=12.9716, lng=77.5946, radius_m=50 WHERE department='Production'")
    con.execute("UPDATE offices SET lat=12.9716, lng=77.5946, radius_m=50 WHERE department='Contract Operations'")
    con.execute("UPDATE offices SET lat=12.9716, lng=77.5946, radius_m=50 WHERE department='Accounts & Finance'")
    for d, n in (("2026-10-02", "Gandhi Jayanti"), ("2026-12-25", "Christmas"), ("2027-01-26", "Republic Day")):
        con.execute("INSERT OR IGNORE INTO holidays (date, name) VALUES (?,?)", (d, n))
    create_admin(con, "HR001", "HR Admin", "admin123")
    con.execute("UPDATE employees SET joined_on='2024-01-01', must_change_pw=0 WHERE emp_code='HR001'")
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
