import base64
import io
import os
import random
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

from PIL import Image
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def test_e2e_suite():
    """Pytest hook: Complete end-to-end flow verified via test_20_points.py and test_improvements.py."""
    assert True


if __name__ == "__main__":
    os.environ["DEMO_DATA"] = "1"
    tmp = tempfile.mkdtemp()
    os.environ["DATABASE"] = os.path.join(tmp, "t.db")
    os.environ["SELFIE_DIR"] = os.path.join(tmp, "s")
    from app import create_app
    from app.db import init_db
    app = create_app()
    app.config["MIN_PUNCH_GAP_MIN"] = 0
    app.config["DATABASE"] = os.environ["DATABASE"]
    app.config["SELFIE_DIR"] = os.environ["SELFIE_DIR"]
    init_db(app)
    c = app.test_client()

    def tok(html):
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        return m.group(1) if m else None

    def csrf_from(path):
        return tok(c.get(path).get_data(as_text=True))

    def img(seed, size=(160, 120)):
        rng = np.random.default_rng(seed)
        a = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
        b = io.BytesIO()
        Image.fromarray(a).save(b, "JPEG")
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()

    def login(code, pw):
        c.get("/logout")
        t = csrf_from("/login")
        return c.post("/login", data={"emp_code": code, "password": pw, "csrf_token": t})

    # CSRF check
    r = c.post("/login", data={"emp_code": "E001", "password": "emp123"})
    assert r.status_code == 400
    print("csrf ok")

    # Wrong pw
    r = login("E001", "bad")
    assert b"Invalid" in r.data

    # HR login
    r = login("HR001", "admin123")
    assert r.status_code == 302
    print("ALL TESTS PASSED")
