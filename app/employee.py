from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for, send_file
from werkzeug.security import check_password_hash

from .auth import employee_required
from .db import get_db
from .services import (audit, build_register, create_notification, d2s, daterange, get_employee_category,
                       get_employee_shift_for_date, get_upcoming_holiday_cached, leave_balance, month_range, now, s2d, today,
                       working_days)

bp = Blueprint("emp", __name__)


def _shift(user, target_date=None, shift_id=None):
    d = target_date or today()
    return get_employee_shift_for_date(user["id"], d, shift_id or user.get("shift_id"))


@bp.route("/dashboard")
@employee_required
def dashboard():
    db, u = get_db(), g.user
    t = today()
    rec = db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (u["id"], d2s(t))).fetchone()
    if not rec:
        yesterday_rec = db.execute(
            "SELECT * FROM attendance WHERE emp_id=? AND date=? AND check_in IS NOT NULL AND check_out IS NULL",
            (u["id"], d2s(t - timedelta(days=1)))
        ).fetchone()
        if yesterday_rec:
            rec = yesterday_rec
    start, end = month_range(t.year, t.month)
    reg_start = min(start, t - timedelta(days=7))
    reg_end = max(end, t)
    reg = build_register([u], reg_start, reg_end)[u["id"]]
    recent = []
    for i in range(0, 7):
        d = t - timedelta(days=i)
        recent.append((d, reg["days"][d], reg["recs"].get(d)))

    # Batch pending counts in a single query
    pend_row = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM leaves WHERE emp_id=? AND status='Pending') as pend_l,
            (SELECT COUNT(*) FROM od_requests WHERE emp_id=? AND status='Pending') as pend_o,
            (SELECT COUNT(*) FROM permission_requests WHERE emp_id=? AND status='Pending') as pend_p
    """, (u["id"], u["id"], u["id"])).fetchone()
    pend = ((pend_row["pend_l"] if pend_row else 0) or 0) + ((pend_row["pend_o"] if pend_row else 0) or 0) + ((pend_row["pend_p"] if pend_row else 0) or 0)

    holiday = get_upcoming_holiday_cached(db)
    od_today = db.execute("SELECT * FROM od_requests WHERE emp_id=? AND od_date=? AND status='Approved'",
                          (u["id"], d2s(t))).fetchone()
    perm_today = db.execute("SELECT * FROM permission_requests WHERE emp_id=? AND perm_date=? AND status='Approved'",
                            (u["id"], d2s(t))).fetchone()
    from .api import get_department_office
    category = get_employee_category(u)
    face_req = db.execute("SELECT * FROM face_change_requests WHERE emp_id=? ORDER BY id DESC LIMIT 1", (u["id"],)).fetchone()
    office = get_department_office(db, u)
    current_shift = _shift(u, t, rec["shift_id"] if (rec and "shift_id" in rec.keys() and rec["shift_id"]) else None)
    return render_template("emp/dashboard.html", rec=rec, shift=current_shift, category=category, summary=reg["summary"], recent=recent,
                           pend=pend, holiday=holiday, od_today=od_today, perm_today=perm_today, face_req=face_req, today=t, month_name=t.strftime("%B %Y"),
                           office=office)


@bp.route("/punch")
@employee_required
def punch():
    db, u = get_db(), g.user
    t = today()
    today_rec = db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (u["id"], d2s(t))).fetchone()
    yesterday_rec = db.execute(
        "SELECT * FROM attendance WHERE emp_id=? AND date=? AND check_in IS NOT NULL AND check_out IS NULL",
        (u["id"], d2s(t - timedelta(days=1)))
    ).fetchone()

    from .api import get_department_office
    office = get_department_office(db, u)
    if yesterday_rec:
        rec = yesterday_rec
        mode = "out"
    elif today_rec and today_rec["check_out"]:
        rec = today_rec
        mode = "done"
    elif today_rec:
        rec = today_rec
        mode = "out"
    else:
        rec = None
        mode = "in"
    category = get_employee_category(u["id"])
    perm_today = db.execute("SELECT * FROM permission_requests WHERE emp_id=? AND perm_date=? AND status='Approved'",
                            (u["id"], d2s(t))).fetchone()
    effective_shift = _shift(u, t, rec["shift_id"] if (rec and "shift_id" in rec.keys() and rec["shift_id"]) else None)
    return render_template("emp/punch.html", rec=rec, mode=mode, office=office, shift=effective_shift, category=category, perm_today=perm_today)


@bp.route("/history")
@employee_required
def history():
    u, t = g.user, today()
    try:
        y, m = map(int, request.args.get("month", t.strftime("%Y-%m")).split("-"))
        start, end = month_range(y, m)
    except Exception:
        y, m = t.year, t.month
        start, end = month_range(y, m)
    reg = build_register([u], start, end)[u["id"]]
    prev = (start - timedelta(days=1)).strftime("%Y-%m")
    nxt = (end + timedelta(days=1)).strftime("%Y-%m")
    category = get_employee_category(u["id"])
    return render_template("emp/history.html", reg=reg, start=start, prev=prev, nxt=nxt, today=t, category=category)


@bp.route("/history/export")
@employee_required
def export_history():
    from .exports import build_employee_attendance_doc
    u, t = g.user, today()
    try:
        y, m = map(int, request.args.get("month", t.strftime("%Y-%m")).split("-"))
        start, end = month_range(y, m)
    except Exception:
        y, m = t.year, t.month
        start, end = month_range(y, m)
    db = get_db()
    emp = db.execute(
        "SELECT e.*, s.name shift_name, s.start_time shift_start, s.end_time shift_end, "
        "c.name category_name, c.code category_code, c.work_hours category_work_hours "
        "FROM employees e LEFT JOIN shifts s ON s.id=e.shift_id "
        "LEFT JOIN employee_categories c ON c.id=e.category_id WHERE e.id=?", (u["id"],)
    ).fetchone() or u
    buf = build_employee_attendance_doc(emp, start, end, db)
    audit("EMPLOYEE_SELF_DOC_EXPORT", "report", f"{u['emp_code']}@{start:%Y-%m}", "via employee history")
    db.commit()
    dl_name = f"{u['emp_code']}_attendance_{start:%Y_%m}.xlsx"
    return send_file(buf, as_attachment=True, download_name=dl_name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/leave", methods=["GET", "POST"])
@employee_required
def leave():
    db, u, t = get_db(), g.user, today()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "cancel":
            lid = request.form.get("id", type=int)
            row = db.execute("SELECT * FROM leaves WHERE id=? AND emp_id=?", (lid, u["id"])).fetchone()
            if row and row["status"] == "Pending":
                db.execute("UPDATE leaves SET status='Cancelled' WHERE id=?", (lid,))
                audit("LEAVE_CANCEL", "leave", lid)
                db.commit()
                flash("Leave request cancelled.", "ok")
            return redirect(url_for("emp.leave"))
        try:
            type_id = int(request.form["type_id"])
            a, b = s2d(request.form["from_date"]), s2d(request.form["to_date"])
        except Exception:
            flash("Please fill in all fields correctly.", "err")
            return redirect(url_for("emp.leave"))
        half = 1 if request.form.get("half_day") else 0
        reason = request.form.get("reason", "").strip()[:500]
        if b < a:
            flash("'To' date cannot be before 'From' date.", "err")
        elif a < t - timedelta(days=30):
            flash("Leave cannot be applied for dates older than 30 days. Contact HR.", "err")
        elif half and a != b:
            flash("Half-day leave must be for a single date.", "err")
        elif not reason:
            flash("Please give a reason.", "err")
        else:
            days = 0.5 if half else working_days(a, b)
            overlap = db.execute(
                "SELECT 1 FROM leaves WHERE emp_id=? AND status IN ('Pending','Approved') AND from_date<=? AND to_date>=?",
                (u["id"], d2s(b), d2s(a))).fetchone()
            bal = next((x for x in leave_balance(u["id"], a.year) if x["id"] == type_id), None)
            if bal is None:
                flash("Invalid leave type.", "err")
            elif days <= 0:
                flash("Selected dates are all holidays / weekly offs.", "err")
            elif overlap:
                flash("You already have a leave request overlapping these dates.", "err")
            elif days > bal["balance"]:
                flash(f"Insufficient {bal['code']} balance ({bal['balance']:g} left).", "err")
            else:
                cur = db.execute(
                    "INSERT INTO leaves (emp_id, type_id, from_date, to_date, days, half_day, reason) VALUES (?,?,?,?,?,?,?)",
                    (u["id"], type_id, d2s(a), d2s(b), days, half, reason))
                audit("LEAVE_APPLY", "leave", cur.lastrowid, f"{a}..{b} days={days}")
                create_notification(
                    title="New Leave Request",
                    message=f"{u['name']} ({u['emp_code']}) applied for {days:g} day(s) leave ({a} to {b}): {reason}",
                    kind="leave",
                    target_role="hr",
                    link_url=url_for("hr.approvals", tab="pending"),
                    db=db
                )
                db.commit()
                flash("Leave request sent to HR.", "ok")
        return redirect(url_for("emp.leave"))
    rows = db.execute("SELECT l.*, t.code, t.name type_name FROM leaves l JOIN leave_types t ON t.id=l.type_id "
                      "WHERE emp_id=? ORDER BY l.id DESC LIMIT 30", (u["id"],)).fetchall()
    types = db.execute("SELECT * FROM leave_types ORDER BY id").fetchall()
    return render_template("emp/leave.html", rows=rows, types=types, balances=leave_balance(u["id"], t.year), today=t)


@bp.route("/od", methods=["GET", "POST"])
@employee_required
def od():
    db, u, t = get_db(), g.user, today()
    if request.method == "POST":
        if request.form.get("action") == "cancel":
            oid = request.form.get("id", type=int)
            row = db.execute("SELECT * FROM od_requests WHERE id=? AND emp_id=?", (oid, u["id"])).fetchone()
            if row and row["status"] == "Pending":
                db.execute("UPDATE od_requests SET status='Cancelled' WHERE id=?", (oid,))
                audit("OD_CANCEL", "od", oid)
                db.commit()
                flash("OD request cancelled.", "ok")
            return redirect(url_for("emp.od"))
        try:
            d = s2d(request.form["od_date"])
        except Exception:
            flash("Please choose a valid date.", "err")
            return redirect(url_for("emp.od"))
        loc = request.form.get("location", "").strip()[:200]
        purpose = request.form.get("purpose", "").strip()[:500]
        if not loc:
            flash("Please enter the OD location / client name.", "err")
        elif d < t - timedelta(days=7):
            flash("OD cannot be applied for dates older than 7 days.", "err")
        elif db.execute("SELECT 1 FROM od_requests WHERE emp_id=? AND od_date=? AND status IN ('Pending','Approved')",
                        (u["id"], d2s(d))).fetchone():
            flash("You already have an OD request for that date.", "err")
        else:
                cur = db.execute(
                    "INSERT INTO od_requests (emp_id, od_date, from_time, to_time, location, purpose) VALUES (?,?,?,?,?,?)",
                    (u["id"], d2s(d), request.form.get("from_time") or None, request.form.get("to_time") or None, loc, purpose))
                audit("OD_APPLY", "od", cur.lastrowid, f"{d} @ {loc}")
                create_notification(
                    title="New On-Duty (OD) Request",
                    message=f"{u['name']} ({u['emp_code']}) requested OD for {d} at {loc}. Purpose: {purpose or 'Field duty'}",
                    kind="od",
                    target_role="hr",
                    link_url=url_for("hr.approvals", tab="pending"),
                    db=db
                )
                db.commit()
                flash("OD request sent to HR. Once approved you can check in from the field location.", "ok")
        return redirect(url_for("emp.od"))
    rows = db.execute("SELECT * FROM od_requests WHERE emp_id=? ORDER BY id DESC LIMIT 30", (u["id"],)).fetchall()
    return render_template("emp/od.html", rows=rows, today=t)


@bp.route("/face-change", methods=["GET", "POST"])
@employee_required
def face_change():
    db, u = get_db(), g.user
    if request.method == "POST":
        action = request.form.get("action")
        if action == "cancel":
            rid = request.form.get("id", type=int)
            row = db.execute("SELECT * FROM face_change_requests WHERE id=? AND emp_id=?", (rid, u["id"])).fetchone()
            if row and row["status"] == "Pending":
                db.execute("UPDATE face_change_requests SET status='Cancelled' WHERE id=?", (rid,))
                audit("FACE_CHANGE_CANCEL", "face_change", rid, f"Employee {u['emp_code']} cancelled request")
                db.commit()
                flash("Face change request cancelled.", "ok")
            return redirect(url_for("emp.face_change"))

        pwd = request.form.get("password", "")
        reason = request.form.get("reason", "").strip()

        # Step 2 of workflow: identity verification with password
        if not check_password_hash(u["password_hash"], pwd):
            flash("Identity verification failed: Incorrect current password.", "err")
            return redirect(url_for("emp.face_change"))

        if not reason or len(reason) < 5:
            flash("Please provide a valid explanation for requesting face re-registration (minimum 5 characters).", "err")
            return redirect(url_for("emp.face_change"))

        existing = db.execute("SELECT id FROM face_change_requests WHERE emp_id=? AND status='Pending'", (u["id"],)).fetchone()
        if existing:
            flash("You already have a pending Face Change request awaiting HR review.", "warn")
            return redirect(url_for("emp.face_change"))

        cur = db.execute(
            "INSERT INTO face_change_requests (emp_id, reason, status) VALUES (?,?, 'Pending')",
            (u["id"], reason)
        )
        audit("FACE_CHANGE_REQUEST", "face_change", cur.lastrowid, f"Employee {u['emp_code']}: {reason}")
        create_notification(
            title="New Face Change Request",
            message=f"{u['name']} ({u['emp_code']}) requested face re-registration. Reason: {reason}",
            kind="face_change",
            target_role="hr",
            link_url=url_for("hr.approvals", tab="pending"),
            db=db
        )
        db.commit()
        flash("Face change request submitted successfully. Once HR approves, you will be able to register your new face.", "ok")
        return redirect(url_for("emp.face_change"))

    rows = db.execute("SELECT * FROM face_change_requests WHERE emp_id=? ORDER BY id DESC LIMIT 20", (u["id"],)).fetchall()
    latest = rows[0] if rows else None
    # Can re-register if approved and face_registered is 0 (cleared upon approval)
    can_reregister = bool(latest and latest["status"] == "Approved" and not u.get("face_registered"))
    return render_template("emp/face_change.html", rows=rows, latest=latest, can_reregister=can_reregister)


@bp.route("/permission", methods=["GET", "POST"])
@employee_required
def permission():
    db, u, t = get_db(), g.user, today()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "cancel":
            pid = request.form.get("id", type=int)
            row = db.execute("SELECT * FROM permission_requests WHERE id=? AND emp_id=?", (pid, u["id"])).fetchone()
            if row and row["status"] == "Pending":
                db.execute("UPDATE permission_requests SET status='Cancelled' WHERE id=?", (pid,))
                audit("PERMISSION_CANCEL", "permission", pid, f"Employee {u['emp_code']} cancelled permission")
                db.commit()
                flash("Permission request cancelled.", "ok")
            return redirect(url_for("emp.permission"))

        try:
            d = s2d(request.form["perm_date"])
        except Exception:
            flash("Please choose a valid date.", "err")
            return redirect(url_for("emp.permission"))

        perm_type = request.form.get("perm_type", "Late Coming").strip()[:50]
        try:
            duration = float(request.form.get("duration_hours", 2.0))
        except (ValueError, TypeError):
            duration = 2.0
        reason = request.form.get("reason", "").strip()[:500]
        from_time = request.form.get("from_time") or None
        to_time = request.form.get("to_time") or None

        if not reason:
            flash("Please enter the reason for permission (e.g. Bank work, personal emergency).", "err")
        elif d < t - timedelta(days=7):
            flash("Permission cannot be applied for dates older than 7 days.", "err")
        elif db.execute("SELECT 1 FROM permission_requests WHERE emp_id=? AND perm_date=? AND status IN ('Pending','Approved')",
                        (u["id"], d2s(d))).fetchone():
            flash("You already have a permission request for that date.", "err")
        else:
            cur = db.execute(
                "INSERT INTO permission_requests (emp_id, perm_date, perm_type, duration_hours, from_time, to_time, reason) "
                "VALUES (?,?,?,?,?,?,?)",
                (u["id"], d2s(d), perm_type, duration, from_time, to_time, reason)
            )
            audit("PERMISSION_APPLY", "permission", cur.lastrowid, f"{d} {perm_type} {duration}h: {reason}")
            create_notification(
                title="New Permission Request",
                message=f"{u['name']} ({u['emp_code']}) requested {duration:g}h {perm_type} for {d}: {reason}",
                kind="permission",
                target_role="hr",
                link_url=url_for("hr.approvals", tab="pending"),
                db=db
            )
            db.commit()
            flash(f"Late Come Permission request ({duration:g} hrs for '{reason}') sent to HR for approval.", "ok")
        return redirect(url_for("emp.permission"))

    rows = db.execute("SELECT * FROM permission_requests WHERE emp_id=? ORDER BY id DESC LIMIT 30", (u["id"],)).fetchall()
    return render_template("emp/permission.html", rows=rows, today=t)

