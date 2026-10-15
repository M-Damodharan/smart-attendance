import io
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for
from werkzeug.security import generate_password_hash

from .auth import hr_required
from .db import get_db
from .services import (coords_from_maps_link, audit, build_register, d2s, daterange,
                       finalize_attendance, format_duration, get_employee_category,
                       get_employee_shift_for_date, month_range, now, s2d, today,
                       DT_FMT, STATUS_LABELS)

bp = Blueprint("hr", __name__, url_prefix="/hr")


def _employees(only_active=True, shift_id=None, dept=None):
    q = ("SELECT e.*, s.name shift_name, c.name category_name, c.work_hours category_work_hours, c.code category_code FROM employees e "
         "LEFT JOIN shifts s ON s.id=e.shift_id "
         "LEFT JOIN employee_categories c ON c.id=e.category_id "
         "WHERE e.role='employee'")
    args = []
    if only_active:
        q += " AND e.active=1"
    if shift_id:
        q += " AND e.shift_id=?"
        args.append(shift_id)
    if dept:
        q += " AND e.department=?"
        args.append(dept)
    return get_db().execute(q + " ORDER BY e.emp_code", args).fetchall()


# ------------------------------------------------------------------ dashboard
@bp.route("/")
@hr_required
def dashboard():
    db, t = get_db(), today()
    emps = _employees()
    reg = build_register(emps, t, t)
    shifts = db.execute("SELECT * FROM shifts WHERE active=1 ORDER BY id").fetchall()
    stats = {"present": 0, "absent": 0, "late": 0, "leave": 0, "od": 0, "working": 0}
    by_shift = {s["id"]: {"shift": s, "present": 0, "absent": 0, "total": 0} for s in shifts}
    for e in emps:
        code = reg[e["id"]]["days"][t]
        rec = reg[e["id"]]["recs"].get(t)
        present = code in ("P", "OT", "HD", "IN", "MP")
        bs = by_shift.get(e["shift_id"])
        if bs:
            bs["total"] += 1
        if present:
            stats["present"] += 1
            if bs:
                bs["present"] += 1
        elif code in ("L", "HL"):
            stats["leave"] += 1
        elif code == "OD":
            stats["od"] += 1
        else:
            stats["absent"] += 1
            if bs and code == "-":
                bs["absent"] += 1
        if code == "IN":
            stats["working"] += 1
        if rec and rec["late"]:
            stats["late"] += 1
    pending_leave = db.execute("SELECT COUNT(*) FROM leaves WHERE status='Pending'").fetchone()[0]
    pending_od = db.execute("SELECT COUNT(*) FROM od_requests WHERE status='Pending'").fetchone()[0]
    recent = db.execute(
        "SELECT a.*, e.name, e.emp_code FROM attendance a JOIN employees e ON e.id=a.emp_id "
        "WHERE a.date=? ORDER BY COALESCE(a.check_out, a.check_in) DESC LIMIT 8", (d2s(t),)).fetchall()
    office_unset = db.execute("SELECT COUNT(*) FROM offices WHERE active=1 AND lat=0 AND lng=0").fetchone()[0] > 0
    return render_template("hr/dashboard.html", office_unset=office_unset, stats=stats, by_shift=by_shift.values(), total=len(emps),
                           pending_leave=pending_leave, pending_od=pending_od, recent=recent, today=t)


# ------------------------------------------------------------------ employee master
@bp.route("/employees")
@hr_required
def employees():
    rows = get_db().execute(
        "SELECT e.*, s.name shift_name, o.name office_name, c.name category_name FROM employees e "
        "LEFT JOIN shifts s ON s.id=e.shift_id LEFT JOIN offices o ON o.id=e.office_id "
        "LEFT JOIN employee_categories c ON c.id=e.category_id ORDER BY e.role DESC, e.emp_code"
    ).fetchall()
    return render_template("hr/employees.html", rows=rows)


@bp.route("/employees/new", methods=["GET", "POST"])
@bp.route("/employees/<int:eid>", methods=["GET", "POST"])
@hr_required
def employee_form(eid=None):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone() if eid else None
    if eid and not emp:
        abort(404)
    if request.method == "POST":
        f = request.form
        code = f.get("emp_code", "").strip().upper()
        name = f.get("name", "").strip()
        role = f.get("role", "employee")
        category_id = f.get("category_id", type=int)
        if not category_id:
            # Fallback based on role
            cat_code = "STAFF" if role == "hr" else "EMPLOYEES"
            crow = db.execute("SELECT id FROM employee_categories WHERE code=?", (cat_code,)).fetchone()
            category_id = crow[0] if crow else None

        if not code or not name or role not in ("employee", "hr"):
            flash("Employee code and name are required.", "err")
        else:
            vals = (code, name, f.get("email", "").strip(), f.get("phone", "").strip(), f.get("department", "").strip(),
                    f.get("designation", "").strip(), role, f.get("shift_id", type=int), f.get("office_id", type=int),
                    category_id, f.get("joined_on") or d2s(today()), 1 if f.get("active") else 0)
            try:
                if emp:
                    db.execute("UPDATE employees SET emp_code=?, name=?, email=?, phone=?, department=?, designation=?, "
                               "role=?, shift_id=?, office_id=?, category_id=?, joined_on=?, active=? WHERE id=?", (*vals, emp["id"]))
                    audit("EMPLOYEE_UPDATE", "employee", emp["id"], f"{code} {name} (cat={category_id})")
                    flash("Employee updated successfully.", "ok")
                else:
                    pw = f.get("password") or "Welcome@123"
                    cur = db.execute(
                        "INSERT INTO employees (emp_code, name, email, phone, department, designation, role, shift_id, "
                        "office_id, category_id, joined_on, active, password_hash, must_change_pw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)",
                        (*vals, generate_password_hash(pw)))
                    audit("EMPLOYEE_CREATE", "employee", cur.lastrowid, f"{code} {name} (cat={category_id})")
                    flash(f"Employee created. Temporary password: {pw}", "ok")
                db.commit()
                return redirect(url_for("hr.employees"))
            except sqlite3.IntegrityError:
                flash("That employee code already exists.", "err")
    shifts = db.execute("SELECT * FROM shifts WHERE active=1").fetchall()
    offices = db.execute("SELECT * FROM offices WHERE active=1").fetchall()
    categories = db.execute("SELECT * FROM employee_categories WHERE active=1 ORDER BY id").fetchall()
    schedules = []
    if emp:
        schedules = db.execute(
            "SELECT w.*, s.name shift_name FROM weekly_shift_schedules w JOIN shifts s ON s.id=w.shift_id "
            "WHERE w.emp_id=? ORDER BY w.start_date DESC LIMIT 10", (emp["id"],)
        ).fetchall()
    return render_template("hr/employee_form.html", emp=emp, shifts=shifts, offices=offices, categories=categories, schedules=schedules)


@bp.post("/employees/<int:eid>/reset-password")
@hr_required
def reset_password(eid):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone() or abort(404)
    pw = "Welcome@123"
    db.execute("UPDATE employees SET password_hash=?, must_change_pw=1 WHERE id=?", (generate_password_hash(pw), eid))
    audit("PASSWORD_RESET", "employee", eid, emp["emp_code"])
    db.commit()
    flash(f"Password for {emp['emp_code']} reset to {pw} (must change at next login).", "ok")
    return redirect(url_for("hr.employee_form", eid=eid))


# ------------------------------------------------------------------ daily attendance (Shift 1 / Shift 2)
@bp.route("/attendance")
@hr_required
def attendance():
    db = get_db()
    try:
        d = s2d(request.args.get("date") or d2s(today()))
    except ValueError:
        d = today()
    emps = _employees()
    reg = build_register(emps, d, d)
    shifts = db.execute("SELECT * FROM shifts WHERE active=1 ORDER BY id").fetchall()
    groups = []
    total_present = 0
    total_not_in = 0
    total_leave = 0
    for s in shifts:
        rows = []
        for e in [x for x in emps if x["shift_id"] == s["id"]]:
            r = reg[e["id"]]
            code = r["days"][d]
            is_present = code in ("P", "OT", "HD", "IN", "MP")
            is_not_in = code == "-"
            is_leave = code in ("L", "HL", "OD")
            rows.append({
                "emp": e,
                "code": code,
                "rec": r["recs"].get(d),
                "is_present": is_present,
                "is_not_in": is_not_in,
                "is_leave": is_leave,
            })
        present = sum(1 for r in rows if r["is_present"])
        not_in = sum(1 for r in rows if r["is_not_in"])
        leave = sum(1 for r in rows if r["is_leave"])
        absent = sum(1 for r in rows if r["code"] == "A")
        total_present += present
        total_not_in += not_in
        total_leave += leave
        groups.append({
            "shift": s,
            "rows": rows,
            "present": present,
            "not_in": not_in,
            "leave": leave,
            "absent": absent,
        })
    return render_template("hr/attendance.html", groups=groups, d=d,
                           total_present=total_present, total_not_in=total_not_in, total_leave=total_leave,
                           prev=d2s(d - timedelta(days=1)), nxt=d2s(d + timedelta(days=1)))


# ------------------------------------------------------------------ approvals
@bp.route("/approvals")
@hr_required
def approvals():
    db = get_db()
    tab = request.args.get("tab", "pending")
    cond = "= 'Pending'" if tab == "pending" else "<> 'Pending'"
    leaves = db.execute(
        f"SELECT l.*, e.name, e.emp_code, t.code FROM leaves l JOIN employees e ON e.id=l.emp_id "
        f"JOIN leave_types t ON t.id=l.type_id WHERE l.status {cond} ORDER BY l.id DESC LIMIT 100").fetchall()
    ods = db.execute(
        f"SELECT o.*, e.name, e.emp_code FROM od_requests o JOIN employees e ON e.id=o.emp_id "
        f"WHERE o.status {cond} ORDER BY o.id DESC LIMIT 100").fetchall()
    return render_template("hr/approvals.html", leaves=leaves, ods=ods, tab=tab)


@bp.post("/approvals/<kind>/<int:rid>")
@hr_required
def decide(kind, rid):
    db = get_db()
    table = {"leave": "leaves", "od": "od_requests"}.get(kind) or abort(404)
    decision = request.form.get("decision")
    if decision not in ("Approved", "Rejected"):
        abort(400)
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone() or abort(404)
    if row["status"] != "Pending":
        flash("This request was already processed.", "warn")
    else:
        db.execute(f"UPDATE {table} SET status=?, decided_by=?, decided_at=?, hr_remarks=? WHERE id=?",
                   (decision, g.user["id"], now().strftime(DT_FMT), request.form.get("remarks", "").strip()[:300], rid))
        audit(f"{kind.upper()}_{decision.upper()}", kind, rid, request.form.get("remarks", "").strip()[:300])
        db.commit()
        flash(f"{kind.title()} request {decision.lower()}.", "ok")
    return redirect(url_for("hr.approvals", tab=request.args.get("tab", "pending")))


# ------------------------------------------------------------------ corrections
@bp.route("/corrections", methods=["GET", "POST"])
@hr_required
def corrections():
    db = get_db()
    if request.method == "POST":
        f = request.form
        try:
            eid = int(f["emp_id"])
            d = s2d(f["date"])
        except Exception:
            flash("Choose an employee and a date.", "err")
            return redirect(url_for("hr.corrections"))
        reason = f.get("reason", "").strip()
        emp = db.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone() or abort(404)
        old = db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (eid, d2s(d))).fetchone()
        if len(reason) < 5:
            flash("A reason (min 5 characters) is mandatory for every correction.", "err")
        elif d > today():
            flash("Cannot correct a future date.", "err")
        elif f.get("delete"):
            if old:
                db.execute("DELETE FROM attendance WHERE id=?", (old["id"],))
                audit("CORRECTION_DELETE", "attendance", f"{emp['emp_code']}@{d}",
                      f"in={old['check_in']} out={old['check_out']} status={old['status']} | reason: {reason}")
                db.commit()
                flash("Attendance record removed.", "ok")
            else:
                flash("No record exists for that day.", "warn")
        else:
            tin, tout = f.get("check_in"), f.get("check_out")
            if not tin:
                flash("Check-in time is required.", "err")
                return redirect(url_for("hr.corrections"))
            cin = f"{d2s(d)} {tin}:00"
            cout = f"{d2s(d)} {tout}:00" if tout else None
            if cout and cout <= cin:
                flash("Check-out must be after check-in.", "err")
                return redirect(url_for("hr.corrections"))
            shift = get_employee_shift_for_date(emp["id"], d, emp["shift_id"])
            cat = get_employee_category(emp["id"])
            upd = finalize_attendance({"check_in": cin, "check_out": cout}, shift, cat)
            if old:
                db.execute("UPDATE attendance SET check_in=?, check_out=?, work_hours=?, ot_hours=?, status=?, late=?, "
                           "source='correction', remarks=? WHERE id=?",
                           (cin, cout, upd["work_hours"], upd["ot_hours"], upd["status"], upd["late"], reason, old["id"]))
            else:
                db.execute("INSERT INTO attendance (emp_id, date, check_in, check_out, work_hours, ot_hours, status, late, "
                           "source, remarks) VALUES (?,?,?,?,?,?,?,?, 'correction', ?)",
                           (eid, d2s(d), cin, cout, upd["work_hours"], upd["ot_hours"], upd["status"], upd["late"], reason))
            audit("CORRECTION", "attendance", f"{emp['emp_code']}@{d}",
                  f"old: in={old['check_in'] if old else None} out={old['check_out'] if old else None} "
                  f"status={old['status'] if old else None} -> new: in={cin} out={cout} status={upd['status']} | reason: {reason}")
            db.commit()
            flash(f"Correction saved. New status: {upd['status'] or 'pending checkout'}.", "ok")
        return redirect(url_for("hr.corrections"))
    recent = db.execute("SELECT * FROM audit_log WHERE action LIKE 'CORRECTION%' ORDER BY id DESC LIMIT 20").fetchall()
    return render_template("hr/corrections.html", emps=_employees(), recent=recent, today=today())


# ------------------------------------------------------------------ audit log
@bp.route("/audit")
@hr_required
def audit_log():
    q, action = request.args.get("q", "").strip(), request.args.get("action", "")
    sql, args = "SELECT * FROM audit_log WHERE 1=1", []
    if q:
        sql += " AND (actor_name LIKE ? OR details LIKE ? OR entity_id LIKE ?)"
        args += [f"%{q}%"] * 3
    if action:
        sql += " AND action=?"
        args.append(action)
    rows = get_db().execute(sql + " ORDER BY id DESC LIMIT 300", args).fetchall()
    actions = [r[0] for r in get_db().execute("SELECT DISTINCT action FROM audit_log ORDER BY action")]
    return render_template("hr/audit.html", rows=rows, actions=actions, q=q, action=action)


# ------------------------------------------------------------------ monthly reports + Excel
def _report_ctx():
    t = today()
    try:
        y, m = map(int, request.args.get("month", t.strftime("%Y-%m")).split("-"))
        start, end = month_range(y, m)
    except Exception:
        start, end = month_range(t.year, t.month)
    shift_id = request.args.get("shift", type=int)
    dept = request.args.get("dept") or None
    emps = _employees(shift_id=shift_id, dept=dept)
    reg = build_register(emps, start, end)
    return start, end, emps, reg, shift_id, dept


@bp.route("/reports")
@hr_required
def reports():
    start, end, emps, reg, shift_id, dept = _report_ctx()
    db = get_db()
    shifts = db.execute("SELECT * FROM shifts WHERE active=1").fetchall()
    depts = [r[0] for r in db.execute("SELECT DISTINCT department FROM employees WHERE department<>'' ORDER BY 1")]
    return render_template("hr/reports.html", start=start, end=end, emps=emps, reg=reg, shifts=shifts, depts=depts,
                           shift_id=shift_id, dept=dept, days=list(daterange(start, end)),
                           prev=(start - timedelta(days=1)).strftime("%Y-%m"), nxt=(end + timedelta(days=1)).strftime("%Y-%m"))


@bp.route("/reports/export")
@hr_required
def export():
    from .exports import build_workbook
    start, end, emps, reg, shift_id, dept = _report_ctx()
    buf = build_workbook(start, end, emps, reg, get_db())
    audit("REPORT_EXPORT", "report", start.strftime("%Y-%m"), f"shift={shift_id} dept={dept}")
    get_db().commit()
    return send_file(buf, as_attachment=True, download_name=f"attendance_{start:%Y_%m}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ settings (offices, shifts, holidays, leave types)
@bp.route("/settings", methods=["GET", "POST"])
@hr_required
def settings():
    db = get_db()
    if request.method == "POST":
        f, act = request.form, request.form.get("action")
        try:
            if act == "office":
                oid = f.get("id", type=int)
                vals = (f["name"].strip(), float(f["lat"]), float(f["lng"]), int(f["radius_m"]), 1 if f.get("active") else 0)
                if oid:
                    db.execute("UPDATE offices SET name=?, lat=?, lng=?, radius_m=?, active=? WHERE id=?", (*vals, oid))
                else:
                    oid = db.execute("INSERT INTO offices (name, lat, lng, radius_m, active) VALUES (?,?,?,?,?)", vals).lastrowid
                audit("OFFICE_SAVE", "office", oid, f"{vals[0]} {vals[1]},{vals[2]} r={vals[3]}m")
            elif act == "office_link":
                pt = coords_from_maps_link(f.get("link", ""))
                if not pt:
                    flash("Could not read coordinates from that link. Paste the full Google Maps URL, "
                          "'lat,lng', or use the 'Use my current location' button.", "err")
                    return redirect(url_for("hr.settings"))
                name = f.get("name", "").strip() or "Office"
                oid = f.get("id", type=int)
                if oid:
                    db.execute("UPDATE offices SET lat=?, lng=? WHERE id=?", (pt[0], pt[1], oid))
                else:
                    oid = db.execute("INSERT INTO offices (name, lat, lng, radius_m) VALUES (?,?,?,?)",
                                     (name, pt[0], pt[1], f.get("radius_m", type=int) or 150)).lastrowid
                audit("OFFICE_LOCATION_FROM_LINK", "office", oid, f"{pt[0]:.6f},{pt[1]:.6f}")
                db.commit()
                flash(f"Office location set to {pt[0]:.6f}, {pt[1]:.6f}.", "ok")
                return redirect(url_for("hr.settings"))
            elif act == "shift":
                sid = f.get("id", type=int)
                vals = (f["name"].strip(), f["start_time"], f["end_time"], int(f["grace_min"]), float(f["full_hours"]),
                        float(f["half_hours"]), float(f["ot_min_hours"]), 1 if f.get("active") else 0)
                if sid:
                    db.execute("UPDATE shifts SET name=?, start_time=?, end_time=?, grace_min=?, full_hours=?, half_hours=?, "
                               "ot_min_hours=?, active=? WHERE id=?", (*vals, sid))
                else:
                    sid = db.execute("INSERT INTO shifts (name, start_time, end_time, grace_min, full_hours, half_hours, "
                                     "ot_min_hours, active) VALUES (?,?,?,?,?,?,?,?)", vals).lastrowid
                audit("SHIFT_SAVE", "shift", sid, " | ".join(map(str, vals)))
            elif act == "category":
                cid = f.get("id", type=int)
                name = f["name"].strip()
                code = f["code"].strip().upper()
                work_h = float(f["work_hours"])
                ot_h = float(f.get("overtime_hours", 0.0) or 0.0)
                half_h = float(f.get("half_day_min_hours", 4.0) or 4.0)
                half_t = f.get("half_day_time", "14:00").strip()
                cutoff_full = f.get("early_full_day_cutoff", "16:00").strip()
                full_t = f.get("full_day_time", "20:00").strip()
                active = 1 if f.get("active") else 0
                vals = (name, code, work_h, ot_h, half_h, half_t, cutoff_full, full_t, active)
                if cid:
                    db.execute(
                        "UPDATE employee_categories SET name=?, code=?, work_hours=?, overtime_hours=?, half_day_min_hours=?, "
                        "half_day_time=?, early_full_day_cutoff=?, full_day_time=?, active=? WHERE id=?",
                        (*vals, cid)
                    )
                    audit("CATEGORY_SAVE", "category", cid, f"{name} {code} {work_h}h OT={ot_h}h")
                else:
                    cid = db.execute(
                        "INSERT INTO employee_categories (name, code, work_hours, overtime_hours, half_day_min_hours, half_day_time, early_full_day_cutoff, full_day_time, active) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        vals
                    ).lastrowid
                    audit("CATEGORY_SAVE", "category", cid, f"{name} {code} {work_h}h OT={ot_h}h")
            elif act == "weekly_schedule":
                target_emp_id = f.get("emp_id", type=int)
                target_shift_id = f.get("shift_id", type=int)
                start_d = f["start_date"].strip()
                end_d = f["end_date"].strip()
                notes = f.get("notes", "").strip()
                if not (target_emp_id and target_shift_id and start_d and end_d):
                    flash("Please select employee, shift, start date, and end date.", "err")
                    return redirect(url_for("hr.settings"))
                if end_d < start_d:
                    flash("End date cannot be earlier than start date.", "err")
                    return redirect(url_for("hr.settings"))
                sc_id = db.execute(
                    "INSERT INTO weekly_shift_schedules (emp_id, shift_id, start_date, end_date, notes) VALUES (?,?,?,?,?)",
                    (target_emp_id, target_shift_id, start_d, end_d, notes)
                ).lastrowid
                audit("WEEKLY_SCHEDULE_ASSIGN", "weekly_shift_schedules", sc_id, f"emp={target_emp_id} shift={target_shift_id} {start_d}..{end_d}")
            elif act == "delete_schedule":
                sc_id = f.get("id", type=int)
                db.execute("DELETE FROM weekly_shift_schedules WHERE id=?", (sc_id,))
                audit("WEEKLY_SCHEDULE_DELETE", "weekly_shift_schedules", sc_id)
            elif act == "holiday":
                s2d(f["date"])
                db.execute("INSERT OR REPLACE INTO holidays (date, name) VALUES (?,?)", (f["date"], f["name"].strip()))
                audit("HOLIDAY_SAVE", "holiday", f["date"], f["name"])
            elif act == "holiday_delete":
                db.execute("DELETE FROM holidays WHERE date=?", (f["date"],))
                audit("HOLIDAY_DELETE", "holiday", f["date"])
            elif act == "leave_type":
                tid = f.get("id", type=int)
                if tid:
                    db.execute("UPDATE leave_types SET name=?, annual_quota=? WHERE id=?", (f["name"].strip(), float(f["quota"]), tid))
                else:
                    tid = db.execute("INSERT INTO leave_types (code, name, annual_quota) VALUES (?,?,?)",
                                     (f["code"].strip().upper(), f["name"].strip(), float(f["quota"]))).lastrowid
                audit("LEAVE_TYPE_SAVE", "leave_type", tid, f"{f['name']} quota={f['quota']}")
            elif act == "system_settings":
                max_acc = int(f.get("max_gps_accuracy_m", 350))
                tol = int(f.get("geofence_tolerance_m", 50))
                db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', ?)", (str(max_acc),))
                db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', ?)", (str(tol),))
                audit("SETTINGS_SAVE", "system_settings", 1, f"max_gps_accuracy_m={max_acc}, geofence_tolerance_m={tol}")
            db.commit()
            flash("Saved successfully.", "ok")
        except (KeyError, ValueError, sqlite3.IntegrityError):
            flash("Please check the values you entered.", "err")
        return redirect(url_for("hr.settings"))
    sys_settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM system_settings").fetchall()}
    categories = db.execute("SELECT * FROM employee_categories ORDER BY id").fetchall()
    weekly_schedules = db.execute(
        "SELECT w.*, e.name emp_name, e.emp_code, s.name shift_name FROM weekly_shift_schedules w "
        "JOIN employees e ON e.id=w.emp_id JOIN shifts s ON s.id=w.shift_id "
        "ORDER BY w.start_date DESC LIMIT 50"
    ).fetchall()
    all_employees = db.execute("SELECT id, emp_code, name FROM employees WHERE active=1 ORDER BY emp_code").fetchall()
    return render_template("hr/settings.html",
                           offices=db.execute("SELECT * FROM offices").fetchall(),
                           shifts=db.execute("SELECT * FROM shifts").fetchall(),
                           categories=categories,
                           weekly_schedules=weekly_schedules,
                           all_employees=all_employees,
                           holidays=db.execute("SELECT * FROM holidays ORDER BY date").fetchall(),
                           ltypes=db.execute("SELECT * FROM leave_types").fetchall(),
                           sys_settings=sys_settings)


# ------------------------------------------------------------------ user access control
@bp.post("/employees/<int:eid>/access")
@hr_required
def toggle_access(eid):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone() or abort(404)
    if emp["id"] == g.user["id"]:
        flash("You cannot revoke your own access.", "err")
    else:
        new = 0 if emp["active"] else 1
        db.execute("UPDATE employees SET active=? WHERE id=?", (new, eid))
        audit("ACCESS_GRANTED" if new else "ACCESS_REVOKED", "employee", eid, emp["emp_code"])
        db.commit()
        flash(f"Access {'granted to' if new else 'revoked for'} {emp['emp_code']}.", "ok")
    return redirect(request.referrer or url_for("hr.employees"))


@bp.post("/employees/<int:eid>/delete")
@hr_required
def delete_employee(eid):
    import os
    from flask import current_app
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone() or abort(404)
    if emp["id"] == g.user["id"]:
        flash("You cannot delete your own account.", "err")
        return redirect(url_for("hr.employees"))
    if request.form.get("confirm", "").strip().upper() != emp["emp_code"]:
        flash("Type the employee code exactly to confirm deletion.", "err")
        return redirect(url_for("hr.employee_form", eid=eid))
    if emp["role"] == "hr" and db.execute("SELECT COUNT(*) FROM employees WHERE role='hr' AND active=1 AND id<>?", (eid,)).fetchone()[0] == 0:
        flash("Cannot delete the last active HR user.", "err")
        return redirect(url_for("hr.employee_form", eid=eid))
    for r in db.execute("SELECT in_selfie, out_selfie FROM attendance WHERE emp_id=?", (eid,)):
        for fn in (r["in_selfie"], r["out_selfie"]):
            if fn:
                try:
                    os.remove(os.path.join(current_app.config["SELFIE_DIR"], os.path.basename(fn)))
                except OSError:
                    pass
    n_att = db.execute("SELECT COUNT(*) FROM attendance WHERE emp_id=?", (eid,)).fetchone()[0]
    for t in ("attendance", "leaves", "od_requests", "challenges"):
        db.execute(f"DELETE FROM {t} WHERE emp_id=?", (eid,))
    db.execute("UPDATE leaves SET decided_by=NULL WHERE decided_by=?", (eid,))
    db.execute("UPDATE od_requests SET decided_by=NULL WHERE decided_by=?", (eid,))
    db.execute("DELETE FROM employees WHERE id=?", (eid,))
    audit("EMPLOYEE_DELETE", "employee", eid, f"{emp['emp_code']} {emp['name']} (removed {n_att} attendance records)")
    db.commit()
    flash(f"{emp['emp_code']} deleted permanently.", "ok")
    return redirect(url_for("hr.employees"))
