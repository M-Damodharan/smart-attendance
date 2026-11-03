"""
Migration script to update Main Office radius to 5m,
set max GPS accuracy to 15m, and sync to Turso cloud database.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import Config

def update_sqlite(db_path):
    if not os.path.exists(db_path):
        print(f"[SKIP] {db_path} does not exist.")
        return
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    # Update Main Office radius to 5m
    cur.execute("UPDATE offices SET radius_m = 5 WHERE name LIKE '%Main Office%' OR id = 1")
    # Update system_settings
    cur.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '15')")
    cur.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")
    con.commit()
    rows = cur.execute("SELECT id, name, radius_m FROM offices").fetchall()
    settings = cur.execute("SELECT key, value FROM system_settings WHERE key IN ('max_gps_accuracy_m', 'geofence_tolerance_m')").fetchall()
    con.close()
    print(f"[OK] Updated SQLite {db_path}:")
    print(f"     Offices: {rows}")
    print(f"     Settings: {settings}")

def update_turso():
    if not (Config.TURSO_DATABASE_URL and Config.TURSO_AUTH_TOKEN):
        print("[SKIP] Turso not configured.")
        return
    try:
        from app.turso_db import connect_turso
        conn = connect_turso(Config.TURSO_DATABASE_URL, Config.TURSO_AUTH_TOKEN)
        conn.execute("UPDATE offices SET radius_m = 5 WHERE name LIKE '%Main Office%' OR id = 1")
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '15')")
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")
        conn.commit()
        rows = conn.execute("SELECT id, name, radius_m FROM offices").fetchall()
        settings = conn.execute("SELECT key, value FROM system_settings WHERE key IN ('max_gps_accuracy_m', 'geofence_tolerance_m')").fetchall()
        print(f"[OK] Updated Turso Cloud DB:")
        print(f"     Offices: {[(r['id'], r['name'], r['radius_m']) for r in rows]}")
        print(f"     Settings: {[(s['key'], s['value']) for s in settings]}")
    except Exception as e:
        print(f"[ERROR] Turso update failed: {e}")

if __name__ == "__main__":
    update_sqlite("instance/attendance.db")
    update_sqlite("app/seed_data/attendance.db")
    update_turso()
    print("Geofence migration to 5 meters completed.")
