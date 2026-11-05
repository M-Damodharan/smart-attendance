import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.turso_db import connect_turso
import config

c = connect_turso(config.Config.TURSO_DATABASE_URL, config.Config.TURSO_AUTH_TOKEN)
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
table_names = [t["name"] for t in tables]
print("Tables in Turso:", table_names)

for t in table_names:
    cnt = c.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()["c"]
    print(f"  {t}: {cnt} rows")
