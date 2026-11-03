import sqlite3
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import Config, BASE_DIR
from app.turso_db import connect_turso

def main():
    print("Connecting to Turso Cloud...")
    c = connect_turso(Config.TURSO_DATABASE_URL, Config.TURSO_AUTH_TOKEN)
    c.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '100')")
    c.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")
    c.execute("UPDATE offices SET radius_m=5 WHERE id=1")
    print("Turso updated successfully.")

    for r in c.execute("SELECT key, value FROM system_settings WHERE key IN ('max_gps_accuracy_m', 'geofence_tolerance_m')").fetchall():
        print(f"Turso setting: {r['key']} = {r['value']}")

    loc_db = os.path.join(BASE_DIR, "instance", "attendance.db")
    if os.path.exists(loc_db):
        con = sqlite3.connect(loc_db)
        con.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '100')")
        con.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")
        con.execute("UPDATE offices SET radius_m=5 WHERE id=1")
        con.commit()
        con.close()
        print("Local instance/attendance.db updated.")

    seed_db = os.path.join(BASE_DIR, "app", "seed_data", "attendance.db")
    if os.path.exists(seed_db):
        con = sqlite3.connect(seed_db)
        con.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', '100')")
        con.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', '0')")
        con.execute("UPDATE offices SET radius_m=5 WHERE id=1")
        con.commit()
        con.close()
        print("Seed app/seed_data/attendance.db updated.")

if __name__ == "__main__":
    main()
