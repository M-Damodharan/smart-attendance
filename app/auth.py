import secrets
import time
from functools import wraps

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .services import audit

bp = Blueprint("auth", __name__)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not g.user:
            return redirect(url_for("auth.login", next=request.path))
        if g.user["must_change_pw"] and request.endpoint not in ("auth.change_password", "auth.logout"):
            flash("Please set a new password to continue.", "warn")
            return redirect(url_for("auth.change_password"))
        return view(*a, **kw)
    return wrapped


def hr_required(view):
    @wraps(view)
    @login_required
    def wrapped(*a, **kw):
        if g.user["role"] != "hr":
            abort(403)
        return view(*a, **kw)
    return wrapped


def employee_required(view):
    @wraps(view)
    @login_required
    def wrapped(*a, **kw):
        if g.user["role"] != "employee":
            return redirect(url_for("hr.dashboard"))
        return view(*a, **kw)
    return wrapped


def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        u = get_db().execute(
            "SELECT e.*, s.name shift_name, s.start_time, s.end_time FROM employees e "
            "LEFT JOIN shifts s ON s.id=e.shift_id WHERE e.id=? AND e.active=1", (uid,)
        ).fetchone()
        if u:
            g.user = u
        else:
            session.clear()


def check_csrf():
    if request.method in ("POST", "PUT", "DELETE"):
        tok = request.headers.get("X-CSRFToken") or request.form.get("csrf_token")
        if not tok or not secrets.compare_digest(tok, session.get("csrf", "")):
            abort(400, "Invalid or missing CSRF token. Refresh the page and try again.")


@bp.route("/", methods=["GET"])
def index():
    if g.user:
        return redirect(url_for("hr.dashboard" if g.user["role"] == "hr" else "emp.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        code = request.form.get("emp_code", "").strip().upper()
        pw = request.form.get("password", "")
        ip = request.remote_addr
        key = f"{code}|{ip}"
        db = get_db()
        row = db.execute("SELECT count, first_ts FROM login_attempts WHERE key=?", (key,)).fetchone()
        cnt, first = (row["count"], row["first_ts"]) if row else (0, time.time())
        if time.time() - first > current_app.config["LOGIN_LOCK_SECONDS"]:
            cnt, first = 0, time.time()
        if cnt >= current_app.config["MAX_LOGIN_ATTEMPTS"]:
            flash("Too many failed attempts. Try again in a few minutes.", "err")
            return render_template("login.html"), 429
        u = get_db().execute("SELECT * FROM employees WHERE emp_code=? AND active=1", (code,)).fetchone()
        if u and check_password_hash(u["password_hash"], pw):
            db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
            session.permanent = True
            session.clear()
            session["uid"], session["name"], session["role"] = u["id"], u["name"], u["role"]
            csrf_token()
            audit("LOGIN", "employee", u["id"], actor=u)
            get_db().commit()
            nxt = request.args.get("next")
            if nxt and nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for("auth.index"))
        db.execute("INSERT INTO login_attempts (key, count, first_ts) VALUES (?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET count=?, first_ts=?", (key, cnt + 1, first, cnt + 1, first))
        db.commit()
        flash("Invalid employee code or password.", "err")
    return render_template("login.html")


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    if g.user:
        audit("LOGOUT", "employee", g.user["id"])
        get_db().commit()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not g.user:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        cur, new, new2 = (request.form.get(k, "") for k in ("current", "new", "confirm"))
        if not check_password_hash(g.user["password_hash"], cur):
            flash("Current password is incorrect.", "err")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "err")
        elif new != new2:
            flash("New passwords do not match.", "err")
        elif new == cur:
            flash("Choose a password different from the current one.", "err")
        else:
            db = get_db()
            db.execute("UPDATE employees SET password_hash=?, must_change_pw=0 WHERE id=?",
                       (generate_password_hash(new), g.user["id"]))
            audit("PASSWORD_CHANGE", "employee", g.user["id"])
            db.commit()
            flash("Password updated.", "ok")
            return redirect(url_for("auth.index"))
    return render_template("change_password.html")
