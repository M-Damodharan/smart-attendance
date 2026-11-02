import os
import sys
import json
import sqlite3
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config
from app.turso_db import connect_turso

def backup_turso():
    c = connect_turso(config.Config.TURSO_DATABASE_URL, config.Config.TURSO_AUTH_TOKEN)
    tables = [t['name'] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() if t['name'] != 'sqlite_sequence']

    backup_data = {}
    os.makedirs('backups/audit_safety', exist_ok=True)
    db_backup_path = 'backups/audit_safety/turso_safety_backup.db'
    if os.path.exists(db_backup_path):
        os.remove(db_backup_path)
    s_conn = sqlite3.connect(db_backup_path)

    for t in tables:
        rows = c.execute(f"SELECT * FROM {t}").fetchall()
        cols = rows[0].keys() if rows else []
        backup_data[t] = [dict(r) for r in rows]
        print(f"Backed up {t}: {len(rows)} rows")
        if rows and cols:
            placeholders = ', '.join(['?'] * len(cols))
            col_names = ', '.join([f'"{col}"' for col in cols])
            s_conn.execute(f'CREATE TABLE IF NOT EXISTS "{t}" ({col_names})')
            for r in rows:
                s_conn.execute(f'INSERT INTO "{t}" ({col_names}) VALUES ({placeholders})', [r[col] for col in cols])
            s_conn.commit()

    with open('backups/audit_safety/turso_safety_backup.json', 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, indent=2)
    s_conn.close()
    print("SUCCESS: Full backup saved to backups/audit_safety/turso_safety_backup.json and .db")

if __name__ == "__main__":
    backup_turso()
