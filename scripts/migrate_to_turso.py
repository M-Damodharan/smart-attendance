"""Migration script to seed the clean Smart Attendance schema and data into Turso Cloud.
Transfers offices, shifts, employee categories, clean employee accounts with registered Face IDs,
and system settings into Turso Cloud database.
"""
import os
import sys
import sqlite3
import json
import urllib.request
import base64

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)
LOCAL_DB = os.path.join(BASE_DIR, "app", "seed_data", "attendance.db")
TOKEN_FILE = os.path.join(BASE_DIR, "instance", ".turso_token")

TURSO_HOST = os.environ.get("TURSO_DATABASE_URL", "sbg-attendance-m-damodharan.aws-ap-south-1.turso.io")
if TURSO_HOST.startswith("libsql://"):
    TURSO_HOST = TURSO_HOST.replace("libsql://", "")
elif TURSO_HOST.startswith("https://"):
    TURSO_HOST = TURSO_HOST.replace("https://", "")

TURSO_URL = f"https://{TURSO_HOST}/v2/pipeline"

with open(TOKEN_FILE, "r") as f:
    TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", f.read().strip())

HEADERS = {
    "Authorization": f"Bearer {TURSO_TOKEN}",
    "Content-Type": "application/json"
}


def execute_turso_batch(stmts):
    requests = []
    for sql, params in stmts:
        stmt = {"sql": sql}
        if params:
            args = []
            for p in params:
                if p is None:
                    args.append({"type": "null"})
                elif isinstance(p, int):
                    args.append({"type": "integer", "value": str(p)})
                elif isinstance(p, float):
                    args.append({"type": "float", "value": p})
                elif isinstance(p, bytes):
                    args.append({"type": "blob", "base64": base64.b64encode(p).decode("ascii")})
                else:
                    args.append({"type": "text", "value": str(p)})
            stmt["args"] = args
        requests.append({"type": "execute", "stmt": stmt})
    requests.append({"type": "close"})

    payload = {"requests": requests}
    req = urllib.request.Request(TURSO_URL, data=json.dumps(payload).encode("utf-8"), headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def migrate():
    print(f"[*] Starting migration to Turso Cloud at {TURSO_HOST}...")
    from app.db import SCHEMA

    # 1. Create all tables on Turso
    print("[*] Creating schema tables on Turso Cloud...")
    for statement in SCHEMA.split(";"):
        stmt = statement.strip()
        if stmt:
            try:
                execute_turso_batch([(stmt, None)])
            except Exception as e:
                print(f"[!] Warning executing statement: {e}")

    # Run migrations for all required columns
    migrations = [
        "ALTER TABLE employees ADD COLUMN block_reason TEXT",
        "ALTER TABLE employees ADD COLUMN blocked_at TEXT",
        "ALTER TABLE employees ADD COLUMN device_registered INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE employees ADD COLUMN device_name TEXT",
        "ALTER TABLE employees ADD COLUMN device_registered_at TEXT",
        "ALTER TABLE attendance ADD COLUMN shift_id INTEGER REFERENCES shifts(id)",
        "ALTER TABLE attendance ADD COLUMN face_verified INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE attendance ADD COLUMN face_similarity REAL NOT NULL DEFAULT 0.0",
        "ALTER TABLE attendance ADD COLUMN auth_method TEXT DEFAULT 'webauthn_face_id'",
        "ALTER TABLE attendance ADD COLUMN device_credential_id TEXT",
        "ALTER TABLE offices ADD COLUMN department TEXT",
    ]
    for mig in migrations:
        try:
            execute_turso_batch([(mig, None)])
        except Exception:
            pass

    # 2. Transfer tables from local clean seed DB
    src_conn = sqlite3.connect(LOCAL_DB)
    src_conn.row_factory = sqlite3.Row
    cur = src_conn.cursor()

    tables = [
        "offices", "shifts", "employee_categories", "leave_types",
        "holidays", "system_settings", "employees"
    ]

    for tbl in tables:
        rows = cur.execute(f"SELECT * FROM {tbl}").fetchall()
        if not rows:
            continue
        cols = list(rows[0].keys())
        col_names = ", ".join(cols)
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT OR REPLACE INTO {tbl} ({col_names}) VALUES ({placeholders})"

        batch = []
        for r in rows:
            batch.append((sql, [r[c] for c in cols]))

        # Send in batches of 50
        for i in range(0, len(batch), 50):
            execute_turso_batch(batch[i:i + 50])

        print(f"  [+] Migrated {len(rows)} records into table '{tbl}'")

    src_conn.close()

    # 3. Verify counts from Turso
    print("[*] Verifying data in Turso Cloud...")
    check_resp = execute_turso_batch([
        ("SELECT COUNT(*) AS cnt FROM employees", None),
        ("SELECT emp_code, name, role, face_registered FROM employees ORDER BY id", None),
        ("SELECT COUNT(*) AS cnt FROM offices", None),
        ("SELECT COUNT(*) AS cnt FROM shifts", None),
    ])

    results = check_resp.get("results", [])
    emp_count = results[0]["response"]["result"]["rows"][0][0]["value"]
    print(f"[SUCCESS] Turso Cloud has {emp_count} active employees:")
    for emp_row in results[1]["response"]["result"]["rows"]:
        code = emp_row[0]["value"]
        name = emp_row[1]["value"]
        role = emp_row[2]["value"]
        face = " [FACE REGISTERED]" if emp_row[3]["value"] == "1" else ""
        print(f"   * {code:<10} | {name:<25} | {role:<10}{face}")

    office_cnt = results[2]["response"]["result"]["rows"][0][0]["value"]
    shift_cnt = results[3]["response"]["result"]["rows"][0][0]["value"]
    print(f"   * Offices: {office_cnt} | Shifts: {shift_cnt}")
    print("\n[COMPLETE] Cloud Database is fully seeded and operational 24/7!")


if __name__ == "__main__":
    migrate()
