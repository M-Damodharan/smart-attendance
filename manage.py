"""Admin command line.

  python manage.py create-admin          create an HR user (prompts for details)
  python manage.py reset-password CODE   set a temporary password for any user
  python manage.py backup [DIR]          consistent backup of database + selfies
"""
import getpass
import os
import shutil
import sqlite3
import sys
from datetime import datetime

from werkzeug.security import generate_password_hash

from config import Config
from app.db import SCHEMA, _seed_base, create_admin


def _con():
    os.makedirs(os.path.dirname(Config.DATABASE), exist_ok=True)
    con = sqlite3.connect(Config.DATABASE)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM shifts").fetchone()[0] == 0:
        _seed_base(con)
    return con


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "create-admin":
        con = _con()
        code = input("HR employee code (e.g. HR001): ").strip().upper()
        name = input("Full name: ").strip()
        pw = getpass.getpass("Temporary password (min 8 chars): ")
        if not code or not name or len(pw) < 8:
            sys.exit("Code, name and a password of 8+ characters are required.")
        try:
            create_admin(con, code, name, pw)
        except sqlite3.IntegrityError:
            sys.exit("That employee code already exists.")
        con.commit()
        print(f"Created HR user {code}. They must change the password at first login.")
    elif cmd == "reset-password" and len(sys.argv) > 2:
        con = _con()
        pw = getpass.getpass("New temporary password: ")
        n = con.execute("UPDATE employees SET password_hash=?, must_change_pw=1 WHERE emp_code=?",
                        (generate_password_hash(pw), sys.argv[2].upper())).rowcount
        con.commit()
        print("Password reset." if n else "No such employee code.")
    elif cmd == "backup":
        dest = os.path.join(sys.argv[2] if len(sys.argv) > 2 else "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(dest, exist_ok=True)
        src = sqlite3.connect(Config.DATABASE)
        dst = sqlite3.connect(os.path.join(dest, "attendance.db"))
        src.backup(dst)                                   # safe while the app is running
        dst.close(); src.close()
        if os.path.isdir(Config.SELFIE_DIR):
            shutil.copytree(Config.SELFIE_DIR, os.path.join(dest, "selfies"))
        print("Backup written to", dest)
    elif cmd == "restore" and len(sys.argv) > 2:
        src_dir = sys.argv[2]
        db_src = os.path.join(src_dir, "attendance.db")
        if not os.path.exists(db_src):
            sys.exit(f"Backup database not found: {db_src}")
        os.makedirs(os.path.dirname(Config.DATABASE), exist_ok=True)
        src = sqlite3.connect(db_src)
        dst = sqlite3.connect(Config.DATABASE)
        src.backup(dst)
        dst.close(); src.close()
        selfies_src = os.path.join(src_dir, "selfies")
        if os.path.isdir(selfies_src):
            os.makedirs(Config.SELFIE_DIR, exist_ok=True)
            for f in os.listdir(selfies_src):
                shutil.copy2(os.path.join(selfies_src, f), os.path.join(Config.SELFIE_DIR, f))
        print("Restored database and selfies successfully from", src_dir)
    else:
        print(__doc__)



if __name__ == "__main__":
    main()
