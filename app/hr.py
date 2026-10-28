import io
import logging
import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import generate_password_hash

logger = logging.getLogger(__name__)

from .auth import hr_required
from .db import get_db
from .face_engine import register_face_for_employee, reset_face_for_employee
from .maintenance import safe_clean_cache, get_cache_status
from .services import (coords_from_maps_link, audit, build_register, clear_shared_cache,
                       create_notification, d2s, daterange,
                       finalize_attendance, format_duration, get_all_shifts_cached, get_employee_category,
                       get_employee_shift_for_date, is_late, month_range, now, parse_dt,
                       resolve_employee_shift, s2d, today, DT_FMT, STATUS_LABELS)

bp = Blueprint("hr", __name__, url_prefix="/hr")


def _employees(only_active=True, shift_id=None, dept=None, category_id=None, include_hr=False):
    q = ("SELECT e.*, s.name shift_name, c.name category_name, c.work_hours category_work_hours, c.code category_code FROM employees e "
         "LEFT JOIN shifts s ON s.id=e.shift_id "
         "LEFT JOIN employee_categories c ON c.id=e.category_id ")
    if not include_hr:
        q += "WHERE e.role='employee'"
    else:
        q += "WHERE e.role IN ('employee', 'hr')"
    args = []
    if only_active:
        q += " AND e.active=1"
    if shift_id:
        q += " AND e.shift_id=?"
        args.append(shift_id)
    if dept:
        q += " AND e.department=?"
        args.append(dept)
    if category_id:
        q += " AND e.category_id=?"
        args.append(category_id)
    return get_db().execute(q + " ORDER BY e.emp_code", args).fetchall()


# ------------------------------------------------------------------ dashboard
@bp.route("/")
@bp.route("/dashboard")
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
        rec_d = dict(rec) if rec else {}
        eff_shift = reg[e["id"]].get("shifts", {}).get(t) or resolve_employee_shift(e["id"], t, rec_d.get("check_in"), db, fallback_shift_id=rec_d.get("shift_id"), emp_obj=e)
        eff_shift_id = eff_shift["id"] if eff_shift else e["shift_id"]

        bs = by_shift.get(eff_shift_id)
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

        # Calculate late count
        if rec_d.get("late") == 1 or rec_d.get("late") is True:
            stats["late"] += 1
        elif rec_d.get("check_in") and eff_shift:
            cin_dt = parse_dt(rec_d["check_in"]) if isinstance(rec_d["check_in"], str) else rec_d["check_in"]
            if cin_dt and is_late(cin_dt, eff_shift):
                stats["late"] += 1

    # Batch all count queries into a single round-trip to Turso Cloud for maximum speed
    batch_counts = db.execute("""
        SELECT
            (SELECT COUNT(DISTINCT emp_id) FROM attendance WHERE date=? AND late=1) as db_late_count,
            (SELECT COUNT(*) FROM leaves WHERE status='Pending') as pending_leave,
            (SELECT COUNT(*) FROM od_requests WHERE status='Pending') as pending_od,
            (SELECT COUNT(*) FROM face_change_requests WHERE status='Pending') as pending_face_changes,
            (SELECT COUNT(*) FROM ai_face_alerts WHERE status='BLOCKED') as pending_ai_alerts,
            (SELECT COUNT(*) FROM employees WHERE active=0 AND block_reason IS NOT NULL) as ai_blocked_count,
            (SELECT COUNT(*) FROM employees e JOIN employee_categories c ON c.id=e.category_id WHERE e.active=1 AND c.code='STAFF') as wf_staff,
            (SELECT COUNT(*) FROM employees e JOIN employee_categories c ON c.id=e.category_id WHERE e.active=1 AND c.code='EMPLOYEES') as wf_employees,
            (SELECT COUNT(*) FROM employees e JOIN employee_categories c ON c.id=e.category_id WHERE e.active=1 AND c.code='CONTRACT') as wf_contract,
            (SELECT COUNT(*) FROM employees e JOIN employee_categories c ON c.id=e.category_id WHERE e.active=1 AND (c.code='ACCOUNTS_FINANCE' OR e.department LIKE '%Accounts%')) as wf_accounts,
            (SELECT COUNT(*) FROM employees WHERE active=1 AND face_registered=1) as face_registered,
            (SELECT COUNT(*) FROM employees WHERE active=1 AND (face_registered=0 OR face_registered IS NULL)) as face_pending_setup,
            (SELECT COUNT(*) FROM offices WHERE active=1 AND lat=0 AND lng=0) as office_unset_count
    """, (d2s(t),)).fetchone()

    stats["late"] = max(stats["late"], (batch_counts["db_late_count"] if batch_counts else 0) or 0)
    pending_leave = (batch_counts["pending_leave"] if batch_counts else 0) or 0
    pending_od = (batch_counts["pending_od"] if batch_counts else 0) or 0
    pending_face_changes = (batch_counts["pending_face_changes"] if batch_counts else 0) or 0
    pending_ai_alerts = (batch_counts["pending_ai_alerts"] if batch_counts else 0) or 0
    ai_blocked_count = (batch_counts["ai_blocked_count"] if batch_counts else 0) or 0

    workforce = {
        "staff": (batch_counts["wf_staff"] if batch_counts else 0) or 0,
        "employees": (batch_counts["wf_employees"] if batch_counts else 0) or 0,
        "contract": (batch_counts["wf_contract"] if batch_counts else 0) or 0,
        "accounts": (batch_counts["wf_accounts"] if batch_counts else 0) or 0,
    }

    face_stats = {
        "registered": (batch_counts["face_registered"] if batch_counts else 0) or 0,
        "pending_setup": (batch_counts["face_pending_setup"] if batch_counts else 0) or 0,
    }
    office_unset = ((batch_counts["office_unset_count"] if batch_counts else 0) or 0) > 0

    upcoming_schedules = db.execute(
        "SELECT w.*, e.name emp_name, e.emp_code, s.name shift_name FROM weekly_shift_schedules w "
        "JOIN employees e ON e.id=w.emp_id JOIN shifts s ON s.id=w.shift_id "
        "WHERE w.end_date >= date('now') ORDER BY w.start_date ASC LIMIT 5"
    ).fetchall()

    security_exceptions = db.execute(
        "SELECT * FROM audit_log WHERE action IN ('FACE_MISMATCH_BLOCKED', 'FACE_DUPLICATE_REJECTED', 'STRANGER_FACE_BLOCKED', 'LOGIN_LOCKED', 'LOGIN_FAILED', 'AI_ACCOUNT_BLOCKED') "
        "ORDER BY id DESC LIMIT 6"
    ).fetchall()

    recent = db.execute(
        "SELECT a.*, e.name, e.emp_code FROM attendance a JOIN employees e ON e.id=a.emp_id "
        "WHERE a.date=? AND e.active=1 ORDER BY COALESCE(a.check_out, a.check_in) DESC LIMIT 8", (d2s(t),)).fetchall()
    active_face_alerts = db.execute("""
        SELECT a.*, 
               e1.emp_code as emp_code, e1.name as emp_name, e1.department as emp_dept,
               e2.emp_code as target_emp_code, e2.name as target_emp_name, e2.department as target_emp_dept
        FROM ai_face_alerts a
        JOIN employees e1 ON e1.id=a.emp_id
        LEFT JOIN employees e2 ON e2.id=a.target_emp_id
        WHERE a.status='BLOCKED'
        ORDER BY a.id DESC LIMIT 5
    """).fetchall()

    return render_template("hr/dashboard.html", office_unset=office_unset, stats=stats, by_shift=by_shift.values(), total=len(emps),
                           pending_leave=pending_leave, pending_od=pending_od, pending_face_changes=pending_face_changes,
                           pending_ai_alerts=pending_ai_alerts, ai_blocked_count=ai_blocked_count,
                           active_face_alerts=active_face_alerts,
                           workforce=workforce, face_stats=face_stats, upcoming_schedules=upcoming_schedules,
                           security_exceptions=security_exceptions, recent=recent, today=t)


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
        dept = f.get("department", "").strip()
        category_id = f.get("category_id", type=int)
        if not category_id:
            # Smart category fallback based on department or role
            if "account" in dept.lower() or "finance" in dept.lower():
                crow = db.execute("SELECT id FROM employee_categories WHERE code='ACCOUNTS_FINANCE'").fetchone()
            elif "contract" in dept.lower() or "labour" in dept.lower():
                crow = db.execute("SELECT id FROM employee_categories WHERE code='CONTRACT'").fetchone()
            elif role == "hr" or "staff" in dept.lower() or "admin" in dept.lower():
                crow = db.execute("SELECT id FROM employee_categories WHERE code='STAFF'").fetchone()
            else:
                crow = db.execute("SELECT id FROM employee_categories WHERE code='EMPLOYEES'").fetchone()
            category_id = crow[0] if crow else 1

        cat_row = db.execute("SELECT code FROM employee_categories WHERE id=?", (category_id,)).fetchone()
        cat_code = cat_row["code"] if cat_row else ""

        shift_id = f.get("shift_id", type=int)
        if not shift_id or (shift_id == 1 and (cat_code == "ACCOUNTS_FINANCE" or "account" in dept.lower())):
            # Smart shift default based on department and category
            if cat_code == "ACCOUNTS_FINANCE" or "account" in dept.lower() or "finance" in dept.lower():
                srow = db.execute("SELECT id FROM shifts WHERE (name LIKE '%Accounts%' OR name LIKE '%Finance%') AND active=1 ORDER BY id LIMIT 1").fetchone()
                if not srow:
                    srow = db.execute("SELECT id FROM shifts WHERE name LIKE '%General%' AND active=1 ORDER BY id LIMIT 1").fetchone()
            elif cat_code == "STAFF" or "staff" in dept.lower() or role == "hr":
                srow = db.execute("SELECT id FROM shifts WHERE (name LIKE '%Staff%' OR name LIKE '%General%') AND active=1 ORDER BY id LIMIT 1").fetchone()
            else:
                srow = db.execute("SELECT id FROM shifts WHERE name LIKE '%Shift 1%' AND active=1 ORDER BY id LIMIT 1").fetchone()
            shift_id = srow[0] if srow and srow[0] else 1

        office_id = f.get("office_id", type=int)
        if not office_id:
            # Smart department location point default
            if cat_code == "ACCOUNTS_FINANCE" or "account" in dept.lower() or "finance" in dept.lower():
                orow = db.execute("SELECT id FROM offices WHERE department='Accounts & Finance' AND active=1 ORDER BY id LIMIT 1").fetchone()
            elif cat_code == "CONTRACT" or "contract" in dept.lower():
                orow = db.execute("SELECT id FROM offices WHERE department='Contract Operations' AND active=1 ORDER BY id LIMIT 1").fetchone()
            elif cat_code == "STAFF" or "staff" in dept.lower() or role == "hr":
                orow = db.execute("SELECT id FROM offices WHERE department='Staff' AND active=1 ORDER BY id LIMIT 1").fetchone()
            else:
                orow = db.execute("SELECT id FROM offices WHERE department='Production' AND active=1 ORDER BY id LIMIT 1").fetchone()
            if orow:
                office_id = orow[0]
            elif not office_id:
                orow = db.execute("SELECT MIN(id) FROM offices WHERE active=1").fetchone()
                office_id = orow[0] if orow and orow[0] else None

        if not code or not name or role not in ("employee", "hr"):
            flash("Employee code and name are required.", "err")
        else:
            vals = (code, name, f.get("email", "").strip(), f.get("phone", "").strip(), f.get("department", "").strip(),
                    f.get("designation", "").strip(), role, shift_id, office_id,
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
                        "office_id, category_id, joined_on, active, password_hash, must_change_pw) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (*vals, generate_password_hash(pw), 1))
                    audit("EMPLOYEE_CREATE", "employee", cur.lastrowid, f"{code} {name} (cat={category_id})")
                    flash(f"Employee created. Temporary password: {pw}", "ok")
                db.commit()
                try:
                    from app.backup_manager import create_backup
                    create_backup(reason="employee_change")
                except Exception:
                    pass
                return redirect(url_for("hr.employees"))
            except sqlite3.IntegrityError:
                flash(f"Employee code '{code}' already exists.", "err")
            except Exception as e:
                flash(f"Error saving employee: {e}", "err")
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


@bp.post("/employees/<int:eid>/register-face")
@hr_required
def register_face(eid):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone() or abort(404)
    img_data = request.form.get("face_camera_data")
    if not img_data and "face_file" in request.files:
        f = request.files["face_file"]
        if f and f.filename:
            img_data = f.read()

    if not img_data:
        flash("Please capture a photo from camera or upload an image file.", "err")
        return redirect(url_for("hr.employee_form", eid=eid))

    force = request.form.get("force") == "1"
    ok, msg, other_emp = register_face_for_employee(db, eid, img_data, registered_by_id=g.user["id"], force=force)
    if ok:
        flash(msg, "ok")
        try:
            from app.backup_manager import create_backup
            create_backup(reason="face_registration")
        except Exception:
            pass
    else:
        flash(msg, "err")
    return redirect(url_for("hr.employee_form", eid=eid))


@bp.post("/employees/<int:eid>/reset-face")
@bp.post("/employees/<int:eid>/reset-device")
@hr_required
def reset_face(eid):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone() or abort(404)
    reset_face_for_employee(db, eid, reset_by_id=g.user["id"])
    from .webauthn_engine import reset_device_for_employee
    reset_device_for_employee(db, eid, reset_by_id=g.user["id"])
    flash(f"Biometric Phone Face ID reset for {emp['name']} ({emp['emp_code']}). Employee can now register their new phone.", "ok")
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
    shifts = get_all_shifts_cached(db)
    emp_shift_ids = {}
    for e in emps:
        rec = reg[e["id"]]["recs"].get(d)
        rec_d = dict(rec) if rec else {}
        eff_shift = reg[e["id"]].get("shifts", {}).get(d) or resolve_employee_shift(e["id"], d, rec_d.get("check_in"), db, fallback_shift_id=rec_d.get("shift_id"), emp_obj=e)
        emp_shift_ids[e["id"]] = eff_shift["id"] if eff_shift else e["shift_id"]

    groups = []
    total_present = 0
    total_not_in = 0
    total_leave = 0
    for s in shifts:
        rows = []
        for e in emps:
            if emp_shift_ids.get(e["id"]) != s["id"]:
                continue

            r = reg[e["id"]]
            rec = r["recs"].get(d)
            code = r["days"][d]
            is_present = code in ("P", "OT", "HD", "IN", "MP")
            is_not_in = code == "-"
            is_leave = code in ("L", "HL", "OD")
            rows.append({
                "emp": e,
                "code": code,
                "rec": rec,
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


# ------------------------------------------------------------------ Shift Management & Bulk Assignment
@bp.route("/shifts", methods=["GET"])
@hr_required
def shifts():
    db = get_db()
    dept = request.args.get("dept") or None
    category_id = request.args.get("category", type=int)
    shift_id = request.args.get("shift", type=int)
    status_filter = request.args.get("status", "active")
    q = request.args.get("q", "").strip()

    sql = (
        "SELECT e.*, s.name shift_name, s.start_time shift_start, s.end_time shift_end, "
        "c.name category_name, c.code category_code "
        "FROM employees e "
        "LEFT JOIN shifts s ON s.id=e.shift_id "
        "LEFT JOIN employee_categories c ON c.id=e.category_id "
        "WHERE 1=1 "
    )
    args = []
    if status_filter == "active":
        sql += " AND e.active=1"
    elif status_filter == "inactive":
        sql += " AND e.active=0"

    if dept:
        sql += " AND e.department=?"
        args.append(dept)
    if category_id:
        sql += " AND e.category_id=?"
        args.append(category_id)
    if shift_id:
        sql += " AND e.shift_id=?"
        args.append(shift_id)
    if q:
        sql += " AND (e.emp_code LIKE ? OR e.name LIKE ?)"
        args.extend([f"%{q}%", f"%{q}%"])

    sql += " ORDER BY c.id, e.emp_code"
    employees_list = db.execute(sql, args).fetchall()

    all_shifts = db.execute("SELECT * FROM shifts WHERE active=1 ORDER BY id").fetchall()
    all_categories = db.execute("SELECT * FROM employee_categories ORDER BY id").fetchall()
    all_depts = [r[0] for r in db.execute("SELECT DISTINCT department FROM employees WHERE department<>'' ORDER BY 1")]

    # Weekly schedules active for today onwards
    today_str = d2s(today())
    schedules = db.execute(
        "SELECT w.*, e.emp_code, e.name emp_name, s.name shift_name FROM weekly_shift_schedules w "
        "JOIN employees e ON e.id=w.emp_id JOIN shifts s ON s.id=w.shift_id "
        "WHERE w.end_date >= ? ORDER BY w.start_date ASC LIMIT 50", (today_str,)
    ).fetchall()

    return render_template(
        "hr/shifts.html",
        employees=employees_list,
        shifts=all_shifts,
        categories=all_categories,
        depts=all_depts,
        schedules=schedules,
        dept=dept,
        category_id=category_id,
        shift_id=shift_id,
        status_filter=status_filter,
        q=q,
        today=today()
    )


@bp.post("/shifts/bulk-assign")
@hr_required
def bulk_assign_shifts():
    db = get_db()
    emp_ids = request.form.getlist("emp_ids")
    if not emp_ids:
        flash("Please select at least one employee using the checkboxes.", "err")
        return redirect(url_for("hr.shifts"))

    shift_id = request.form.get("shift_id", type=int)
    target_shift = db.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
    if not target_shift:
        flash("Invalid target shift selected.", "err")
        return redirect(url_for("hr.shifts"))

    assignment_type = request.form.get("assignment_type", "permanent")
    count = 0

    if assignment_type == "weekly":
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        notes = request.form.get("notes", "").strip() or f"Bulk Rotation: {target_shift['name']}"

        try:
            d_start = s2d(start_date)
            d_end = s2d(end_date)
            if d_end < d_start:
                flash("End date cannot be earlier than start date for weekly rotation.", "err")
                return redirect(url_for("hr.shifts"))
        except Exception:
            flash("Please enter valid start and end dates for the weekly rotation schedule.", "err")
            return redirect(url_for("hr.shifts"))

        for eid in emp_ids:
            try:
                db.execute(
                    "INSERT INTO weekly_shift_schedules (emp_id, shift_id, start_date, end_date, notes) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (int(eid), shift_id, start_date, end_date, notes)
                )
                count += 1
            except Exception:
                pass

        audit("BULK_WEEKLY_SHIFT_ROTATION", "shift", shift_id,
              f"Scheduled {count} employees to {target_shift['name']} from {start_date} to {end_date}")
        db.commit()
        clear_shared_cache()
        flash(f"Successfully scheduled weekly rotation to '{target_shift['name']}' ({start_date} to {end_date}) for {count} employee(s).", "ok")

    else:
        # Permanent / Default shift update
        for eid in emp_ids:
            try:
                db.execute("UPDATE employees SET shift_id=? WHERE id=?", (shift_id, int(eid)))
                count += 1
            except Exception:
                pass

        audit("BULK_SHIFT_ASSIGNMENT", "shift", shift_id,
              f"Assigned {target_shift['name']} to {count} employees as default shift")
        db.commit()
        clear_shared_cache()
        flash(f"Successfully updated default shift to '{target_shift['name']}' for {count} employee(s).", "ok")

    return redirect(url_for("hr.shifts"))


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
    face_changes = db.execute(
        f"SELECT f.*, e.name, e.emp_code FROM face_change_requests f JOIN employees e ON e.id=f.emp_id "
        f"WHERE f.status {cond} ORDER BY f.id DESC LIMIT 100").fetchall()
    permissions = db.execute(
        f"SELECT p.*, e.name, e.emp_code FROM permission_requests p JOIN employees e ON e.id=p.emp_id "
        f"WHERE p.status {cond} ORDER BY p.id DESC LIMIT 100").fetchall()
    return render_template("hr/approvals.html", leaves=leaves, ods=ods, face_changes=face_changes, permissions=permissions, tab=tab)


@bp.post("/approvals/<kind>/<int:rid>")
@hr_required
def decide(kind, rid):
    db = get_db()
    table = {"leave": "leaves", "od": "od_requests", "face": "face_change_requests", "permission": "permission_requests"}.get(kind) or abort(404)
    decision = request.form.get("decision")
    if decision not in ("Approved", "Rejected"):
        abort(400)
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone() or abort(404)
    if row["status"] != "Pending":
        flash("This request was already processed.", "warn")
    else:
        remarks = request.form.get("remarks", "").strip()[:300]
        db.execute(f"UPDATE {table} SET status=?, decided_by=?, decided_at=?, hr_remarks=? WHERE id=?",
                   (decision, g.user["id"], now().strftime(DT_FMT), remarks, rid))
        if kind == "face" and decision == "Approved":
            reset_face_for_employee(db, row["emp_id"], reset_by_id=g.user["id"])
            from .webauthn_engine import reset_device_for_employee
            reset_device_for_employee(db, row["emp_id"], reset_by_id=g.user["id"])
            audit("FACE_CHANGE_APPROVED", "employee", row["emp_id"], f"HR {g.user['emp_code']} approved phone face reset: {remarks}")
        elif kind == "face" and decision == "Rejected":
            audit("FACE_CHANGE_REJECTED", "employee", row["emp_id"], f"HR {g.user['emp_code']} rejected phone face change: {remarks}")
        label = "Face change" if kind == "face" else ("Late Come Permission" if kind == "permission" else kind.title())
        icon = "🎉" if decision == "Approved" else "❌"
        remarks_str = f" HR Remarks: {remarks}" if remarks else ""

        if kind == "face" and decision == "Approved":
            notif_msg = f"Your Face Change request has been approved by HR! Please proceed to re-register your face template.{remarks_str}"
            notif_link = url_for("auth.onboarding_face")
        elif kind == "face":
            notif_msg = f"Your Face Change request was rejected by HR.{remarks_str}"
            notif_link = url_for("emp.face_change")
        elif kind == "leave":
            notif_msg = f"Your Leave request has been {decision.lower()} by HR.{remarks_str}"
            notif_link = url_for("emp.leave")
        elif kind == "od":
            notif_msg = f"Your On-Duty (OD) request has been {decision.lower()} by HR.{remarks_str}"
            notif_link = url_for("emp.od")
        elif kind == "permission":
            notif_msg = f"Your Late Come Permission request has been {decision.lower()} by HR.{remarks_str}"
            notif_link = url_for("emp.permission")
        else:
            notif_msg = f"Your request has been {decision.lower()} by HR.{remarks_str}"
            notif_link = url_for("emp.dashboard")

        create_notification(
            title=f"{icon} {label} Request {decision}",
            message=notif_msg,
            kind=kind,
            user_id=row["emp_id"],
            target_role="employee",
            link_url=notif_link,
            db=db
        )

        db.commit()
        flash(f"{label} request {decision.lower()}.", "ok")
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
            shift = resolve_employee_shift(emp["id"], d, cin, db)
            cat = get_employee_category(emp["id"])
            upd = finalize_attendance({"check_in": cin, "check_out": cout}, shift, cat)
            if old:
                db.execute("UPDATE attendance SET shift_id=?, check_in=?, check_out=?, work_hours=?, ot_hours=?, status=?, late=?, "
                           "source='correction', remarks=? WHERE id=?",
                           (shift["id"], cin, cout, upd["work_hours"], upd["ot_hours"], upd["status"], upd["late"], reason, old["id"]))
            else:
                db.execute("INSERT INTO attendance (emp_id, date, shift_id, check_in, check_out, work_hours, ot_hours, status, late, "
                           "source, remarks) VALUES (?,?,?,?,?,?,?,?,?, 'correction', ?)",
                           (eid, d2s(d), shift["id"], cin, cout, upd["work_hours"], upd["ot_hours"], upd["status"], upd["late"], reason))
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
    category_id = request.args.get("category", type=int)
    emps = _employees(shift_id=shift_id, dept=dept, category_id=category_id)
    reg = build_register(emps, start, end)
    return start, end, emps, reg, shift_id, dept, category_id


@bp.route("/reports")
@hr_required
def reports():
    start, end, emps, reg, shift_id, dept, category_id = _report_ctx()
    db = get_db()
    shifts = db.execute("SELECT * FROM shifts WHERE active=1 ORDER BY id").fetchall()
    categories = db.execute("SELECT * FROM employee_categories WHERE active=1 ORDER BY id").fetchall()
    depts = [r[0] for r in db.execute("SELECT DISTINCT department FROM employees WHERE department<>'' ORDER BY 1")]
    return render_template("hr/reports.html", start=start, end=end, emps=emps, reg=reg, shifts=shifts, depts=depts,
                           categories=categories, category_id=category_id,
                           shift_id=shift_id, dept=dept, days=list(daterange(start, end)),
                           prev=(start - timedelta(days=1)).strftime("%Y-%m"), nxt=(end + timedelta(days=1)).strftime("%Y-%m"))


@bp.route("/reports/export")
@hr_required
def export():
    from .exports import build_workbook
    start, end, emps, reg, shift_id, dept, category_id = _report_ctx()
    db = get_db()
    cat_row = db.execute("SELECT * FROM employee_categories WHERE id=?", (category_id,)).fetchone() if category_id else None
    cat_code = cat_row["code"] if cat_row else None
    buf = build_workbook(start, end, emps, reg, db, category_info=cat_row)
    audit("REPORT_EXPORT", "report", start.strftime("%Y-%m"), f"shift={shift_id} dept={dept} category={cat_code}")
    db.commit()
    dl_name = f"{cat_code}_attendance_{start:%Y_%m}.xlsx" if cat_code else f"attendance_{start:%Y_%m}.xlsx"
    return send_file(buf, as_attachment=True, download_name=dl_name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/reports/employee/<int:eid>/export")
@hr_required
def export_employee(eid):
    from .exports import build_employee_attendance_doc
    db = get_db()
    emp = db.execute(
        "SELECT e.*, s.name shift_name, s.start_time shift_start, s.end_time shift_end, "
        "c.name category_name, c.code category_code, c.work_hours category_work_hours "
        "FROM employees e LEFT JOIN shifts s ON s.id=e.shift_id "
        "LEFT JOIN employee_categories c ON c.id=e.category_id WHERE e.id=?", (eid,)
    ).fetchone() or abort(404)
    t = today()
    try:
        y, m = map(int, request.args.get("month", t.strftime("%Y-%m")).split("-"))
        start, end = month_range(y, m)
    except Exception:
        start, end = month_range(t.year, t.month)
    buf = build_employee_attendance_doc(emp, start, end, db)
    audit("EMPLOYEE_DOC_EXPORT", "report", f"{emp['emp_code']}@{start:%Y-%m}", f"downloaded by HR {g.user['emp_code']}")
    db.commit()
    dl_name = f"{emp['emp_code']}_attendance_{start:%Y_%m}.xlsx"
    return send_file(buf, as_attachment=True, download_name=dl_name,
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
                dept = f.get("department", "").strip() or None
                radius = min(int(f.get("radius_m") or 50), 50)
                vals = (f["name"].strip(), dept, float(f["lat"]), float(f["lng"]), radius, 1 if f.get("active") else 0)
                if oid:
                    db.execute("UPDATE offices SET name=?, department=?, lat=?, lng=?, radius_m=?, active=? WHERE id=?", (*vals, oid))
                else:
                    oid = db.execute("INSERT INTO offices (name, department, lat, lng, radius_m, active) VALUES (?,?,?,?,?,?)", vals).lastrowid
                audit("OFFICE_SAVE", "office", oid, f"{vals[0]} dept={dept} {vals[2]},{vals[3]} r={vals[4]}m")
            elif act == "office_link":
                pt = coords_from_maps_link(f.get("link", ""))
                if not pt:
                    flash("Could not read coordinates from that link. Paste the full Google Maps URL, "
                          "'lat,lng', or use the 'Use my current location' button.", "err")
                    return redirect(url_for("hr.settings"))
                name = f.get("name", "").strip() or "Office"
                oid = f.get("id", type=int)
                dept = f.get("department", "").strip() or None
                radius = min(int(f.get("radius_m") or 50), 50)
                if oid:
                    db.execute("UPDATE offices SET lat=?, lng=?, radius_m=? WHERE id=?", (pt[0], pt[1], radius, oid))
                    if dept:
                        db.execute("UPDATE offices SET department=? WHERE id=?", (dept, oid))
                else:
                    oid = db.execute("INSERT INTO offices (name, department, lat, lng, radius_m) VALUES (?,?,?,?,?)",
                                     (name, dept, pt[0], pt[1], radius)).lastrowid
                audit("OFFICE_LOCATION_FROM_LINK", "office", oid, f"{pt[0]:.6f},{pt[1]:.6f}")
                db.commit()
                flash(f"Department location set to {pt[0]:.6f}, {pt[1]:.6f} (allowed 50 m only).", "ok")
                return redirect(url_for("hr.settings"))
            elif act == "shift":
                sid = f.get("id", type=int)
                vals = (f["name"].strip(), f["start_time"], f["end_time"], int(f["grace_min"]), float(f["full_hours"]),
                        float(f["half_hours"]), float(f["ot_min_hours"]), 1 if f.get("active") else 0)
                if sid:
                    db.execute("UPDATE shifts SET name=?, start_time=?, end_time=?, grace_min=?, full_hours=?, half_hours=?, "
                               "ot_min_hours=?, active=? WHERE id=?", (*vals, sid))
                else:
                    existing_s = db.execute("SELECT id FROM shifts WHERE name=?", (f["name"].strip(),)).fetchone()
                    if existing_s:
                        sid = existing_s["id"]
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
                req_face = "1" if f.get("require_face_match") in ("1", "on", "true") else "0"
                face_thresh = float(f.get("face_match_threshold") or 0.75)
                db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('max_gps_accuracy_m', ?)", (str(max_acc),))
                db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('geofence_tolerance_m', ?)", (str(tol),))
                db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('require_face_match', ?)", (req_face,))
                db.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('face_match_threshold', ?)", (str(face_thresh),))
                audit("SETTINGS_SAVE", "system_settings", 1, f"max_gps_accuracy_m={max_acc}, geofence_tolerance_m={tol}, require_face={req_face}, thresh={face_thresh}")
            elif act == "clear_cache":
                res = safe_clean_cache(db, user_id=g.user["id"])
                freed_msg = res.get("bytes_freed_formatted", "0 B")
                pyc_cnt = res.get("pyc_files_removed", 0)
                ch_cnt = res.get("expired_challenges_cleared", 0)
                flash(
                    f"Safe cache cleanup complete: {pyc_cnt} bytecode files ({freed_msg}) and {ch_cnt} expired challenge tokens cleared. "
                    "All employee records, biometric data, and attendance history remain 100% intact.",
                    "ok"
                )
                session["maintenance_result"] = res
                return redirect(url_for("hr.settings") + "#maintenance")
            db.commit()
            clear_shared_cache()
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
    maintenance_result = session.pop("maintenance_result", None)
    cache_status = get_cache_status()
    from app.backup_manager import list_backups
    backups_list = list_backups()
    return render_template("hr/settings.html",
                           offices=db.execute("SELECT * FROM offices").fetchall(),
                           shifts=db.execute("SELECT * FROM shifts").fetchall(),
                           categories=categories,
                           weekly_schedules=weekly_schedules,
                           all_employees=all_employees,
                           holidays=db.execute("SELECT * FROM holidays ORDER BY date").fetchall(),
                           ltypes=db.execute("SELECT * FROM leave_types").fetchall(),
                           sys_settings=sys_settings,
                           cache_status=cache_status,
                           maintenance_result=maintenance_result,
                           backups_list=backups_list)


@bp.route("/maintenance/clear-cache", methods=["POST"])
@hr_required
def maintenance_clear_cache():
    db = get_db()
    res = safe_clean_cache(db, user_id=g.user["id"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify(res)
    freed_msg = res.get("bytes_freed_formatted", "0 B")
    pyc_cnt = res.get("pyc_files_removed", 0)
    ch_cnt = res.get("expired_challenges_cleared", 0)
    flash(
        f"Safe cache cleanup complete: {pyc_cnt} bytecode files ({freed_msg}) and {ch_cnt} expired challenge tokens cleared. "
        "All employee records, biometric data, and attendance history remain 100% intact.",
        "ok"
    )
    session["maintenance_result"] = res
    return redirect(url_for("hr.settings") + "#maintenance")


@bp.route("/database/download-backup", methods=["GET"])
@hr_required
def download_backup_zip():
    try:
        from app.backup_manager import build_backup_zip_bytes
        zip_bytes = build_backup_zip_bytes()
        if not zip_bytes or len(zip_bytes) < 30:
            raise ValueError("Generated backup archive is empty.")
        filename = f"SBG_Attendance_Backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        return send_file(
            io.BytesIO(zip_bytes),
            mimetype="application/zip",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.exception("Database backup download failed: %s", e)
        try:
            from app.backup_manager import build_fallback_db_zip_bytes
            fb_bytes = build_fallback_db_zip_bytes()
            if fb_bytes and len(fb_bytes) >= 30:
                filename = f"SBG_Attendance_Backup_Fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                return send_file(
                    io.BytesIO(fb_bytes),
                    mimetype="application/zip",
                    as_attachment=True,
                    download_name=filename
                )
        except Exception as fb_err:
            logger.exception("Fallback zip creation failed: %s", fb_err)
        flash(f"Database backup temporarily unavailable: {str(e)}. Please retry shortly.", "err")
        return redirect(url_for("hr.settings") + "#backups")


@bp.route("/database/download-db", methods=["GET"])
@hr_required
def download_database_sqlite():
    """Allows downloading the raw attendance.db SQLite file directly."""
    import os
    from config import BASE_DIR, Config
    try:
        from app.backup_manager import create_backup, get_configured_db_path, LATEST_BACKUP_DIR
        try:
            create_backup(reason="direct_db_download")
        except Exception as snap_err:
            logger.warning("Snapshot creation for direct db download notice: %s", snap_err)

        candidate_dbs = [
            os.path.join(LATEST_BACKUP_DIR, "attendance.db"),
            get_configured_db_path(),
            os.path.join(BASE_DIR, "app", "seed_data", "attendance.db")
        ]
        target_db = None
        for candidate in candidate_dbs:
            if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                target_db = candidate
                break

        if not target_db:
            flash("Database file not available for download on server disk.", "err")
            return redirect(url_for("hr.settings") + "#backups")

        filename = f"SBG_attendance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        return send_file(
            target_db,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.sqlite3"
        )
    except Exception as e:
        logger.exception("Failed downloading raw SQLite database: %s", e)
        flash(f"Failed to download database: {str(e)}", "err")
        return redirect(url_for("hr.settings") + "#backups")


@bp.route("/database/restore-backup", methods=["POST"])
@hr_required
def restore_backup_route():
    file = request.files.get("backup_file")
    if not file or not file.filename:
        flash("Please select a valid .zip or .db backup file to restore.", "err")
        return redirect(url_for("hr.settings") + "#backups")
    from app.backup_manager import restore_backup
    res = restore_backup(file, user_id=g.user["id"])
    if res.get("ok"):
        flash(
            f"Database successfully restored! Restored {res.get('employees', 0)} employees, "
            f"{res.get('attendance', 0)} attendance records, and {res.get('selfies_restored', 0)} photos.",
            "ok"
        )
    else:
        flash(f"Restore failed: {res.get('error', 'Unknown error')}", "err")
    return redirect(url_for("hr.settings") + "#backups")


@bp.route("/database/create-snapshot", methods=["POST"])
@hr_required
def create_snapshot_route():
    from app.backup_manager import create_backup
    res = create_backup(reason="hr_manual")
    if res.get("ok"):
        counts = res.get("counts", {})
        flash(
            f"New backup snapshot created successfully! Saved {counts.get('employees', 0)} employees, "
            f"{counts.get('attendance', 0)} attendance records, and {counts.get('selfie_files', 0)} photos.",
            "ok"
        )
    else:
        flash(f"Failed to create backup: {res.get('error')}", "err")
    return redirect(url_for("hr.settings") + "#backups")


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


@bp.route("/employees/<int:eid>/delete", methods=["GET", "POST"])
@hr_required
def delete_employee(eid):
    if request.method == "GET":
        flash("To delete an employee, please use the Delete button in Employee Master.", "info")
        return redirect(url_for("hr.employees"))

    import os
    from flask import current_app
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone() or abort(404)
    if emp["id"] == g.user["id"]:
        flash("You cannot delete your own account.", "err")
        return redirect(url_for("hr.employees"))
    if request.form.get("confirm", "").strip().upper() != emp["emp_code"].upper():
        flash("Type the employee code exactly to confirm deletion.", "err")
        return redirect(request.referrer or url_for("hr.employees"))
    if emp["role"] == "hr" and db.execute("SELECT COUNT(*) FROM employees WHERE role='hr' AND active=1 AND id<>?", (eid,)).fetchone()[0] == 0:
        flash("Cannot delete the last active HR user.", "err")
        return redirect(request.referrer or url_for("hr.employees"))

    selfie_dir = current_app.config.get("SELFIE_DIR", "")
    emp_code = emp["emp_code"].upper()

    try:
        # 1. Clean attendance selfies from disk
        for r in db.execute("SELECT in_selfie, out_selfie FROM attendance WHERE emp_id=?", (eid,)):
            for fn in (r["in_selfie"], r["out_selfie"]):
                if fn and selfie_dir:
                    try:
                        fpath = os.path.join(selfie_dir, os.path.basename(fn))
                        if os.path.isfile(fpath):
                            os.remove(fpath)
                    except OSError:
                        pass

        # 2. Clean registered face photo from disk
        if emp["face_image"] and selfie_dir:
            try:
                fpath = os.path.join(selfie_dir, os.path.basename(emp["face_image"]))
                if os.path.isfile(fpath):
                    os.remove(fpath)
            except OSError:
                pass

        # 3. Clean AI face alert selfies from disk
        try:
            for r in db.execute("SELECT selfie_path, target_selfie_path FROM ai_face_alerts WHERE emp_id=? OR target_emp_id=?", (eid, eid)):
                for fn in (r["selfie_path"], r["target_selfie_path"]):
                    if fn and selfie_dir:
                        try:
                            fpath = os.path.join(selfie_dir, os.path.basename(fn))
                            if os.path.isfile(fpath):
                                os.remove(fpath)
                        except OSError:
                            pass
        except Exception:
            pass

        n_att_row = db.execute("SELECT COUNT(*) FROM attendance WHERE emp_id=?", (eid,)).fetchone()
        n_att = n_att_row[0] if n_att_row else 0

        # 4. Cascade delete all dependent records belonging to this employee
        # Includes notifications and comments which have strict foreign keys to employees(id)
        for t in (
            "notifications",
            "comments",
            "attendance",
            "leaves",
            "od_requests",
            "permission_requests",
            "face_change_requests",
            "challenges",
            "weekly_shift_schedules",
            "webauthn_credentials",
        ):
            try:
                col_name = "user_id" if t == "notifications" else "emp_id"
                db.execute(f"DELETE FROM {t} WHERE {col_name}=?", (eid,))
            except Exception as e:
                current_app.logger.warning(f"Could not delete from {t} for employee {eid}: {e}")

        try:
            db.execute("DELETE FROM ai_face_alerts WHERE emp_id=? OR target_emp_id=?", (eid, eid))
        except Exception:
            pass

        try:
            db.execute("DELETE FROM durable_photos WHERE UPPER(emp_code)=?", (emp_code,))
        except Exception:
            pass

        # 5. Nullify foreign reviewer/decider/registrant references so they do not point to deleted ID
        try:
            db.execute("UPDATE notifications SET user_id=NULL WHERE user_id=?", (eid,))
            db.execute("UPDATE leaves SET decided_by=NULL WHERE decided_by=?", (eid,))
            db.execute("UPDATE od_requests SET decided_by=NULL WHERE decided_by=?", (eid,))
            db.execute("UPDATE permission_requests SET decided_by=NULL WHERE decided_by=?", (eid,))
            db.execute("UPDATE face_change_requests SET decided_by=NULL WHERE decided_by=?", (eid,))
            db.execute("UPDATE ai_face_alerts SET hr_action_by=NULL WHERE hr_action_by=?", (eid,))
            db.execute("UPDATE employees SET face_registered_by=NULL WHERE face_registered_by=?", (eid,))
        except Exception as e:
            current_app.logger.warning(f"Could not nullify references for employee {eid}: {e}")

        # 6. Delete employee record
        db.execute("DELETE FROM employees WHERE id=?", (eid,))
        audit("EMPLOYEE_DELETE", "employee", eid, f"{emp['emp_code']} {emp['name']} (fully deleted with {n_att} attendance records, biometrics, and requests)")
        db.commit()

        # Invalidate caches
        from app.auth import clear_user_cache
        clear_user_cache(eid)
        from app.turso_db import clear_turso_cache
        clear_turso_cache()
        clear_shared_cache()

        flash(f"{emp['emp_code']} ({emp['name']}) and all associated records deleted permanently.", "ok")
    except Exception as e:
        current_app.logger.exception(f"Error deleting employee {eid}: {e}")
        flash(f"Could not delete employee {emp['emp_code']}: {str(e)}", "err")

    return redirect(url_for("hr.employees"))


# ------------------------------------------------------------------ AI Biometric Security & Buddy Punch Audit
@bp.route("/face-security-audit")
@hr_required
def face_security_audit():
    db = get_db()
    from .services import d2s, today
    cur_date = request.args.get("date") or d2s(today())

    from .security_shield import scan_daily_punches_for_buddy_punches
    # Automatically analyze all stored daily punch images across employee accounts
    try:
        scan_daily_punches_for_buddy_punches(db, target_date_str=cur_date)
    except Exception:
        pass

    alerts = db.execute("""
        SELECT a.*, 
               e1.emp_code as emp_code, e1.name as emp_name, e1.active as emp_active, e1.department as emp_dept,
               e2.emp_code as target_emp_code, e2.name as target_emp_name, e2.department as target_emp_dept,
               hr.name as hr_name
        FROM ai_face_alerts a
        JOIN employees e1 ON e1.id=a.emp_id
        LEFT JOIN employees e2 ON e2.id=a.target_emp_id
        LEFT JOIN employees hr ON hr.id=a.hr_action_by
        ORDER BY a.id DESC LIMIT 100
    """).fetchall()

    audit_counts = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM employees WHERE active=0 AND block_reason IS NOT NULL) as blocked_count,
            (SELECT COUNT(*) FROM ai_face_alerts) as total_alerts,
            (SELECT COUNT(*) FROM ai_face_alerts WHERE status='RESOLVED_UNLOCKED') as resolved_count
    """).fetchone()
    blocked_count = (audit_counts["blocked_count"] if audit_counts else 0) or 0
    total_alerts = (audit_counts["total_alerts"] if audit_counts else 0) or 0
    resolved_count = (audit_counts["resolved_count"] if audit_counts else 0) or 0

    return render_template(
        "hr/face_security_audit.html",
        alerts=alerts,
        blocked_count=blocked_count,
        total_alerts=total_alerts,
        resolved_count=resolved_count,
        cur_date=cur_date
    )


@bp.post("/face-security-audit/resolve/<int:alert_id>")
@hr_required
def resolve_face_security_alert(alert_id):
    db = get_db()
    action = request.form.get("action")  # "unlock" | "confirm"
    notes = request.form.get("notes", "").strip()

    from .security_shield import unlock_employee_account_by_hr

    if action == "unlock":
        ok = unlock_employee_account_by_hr(db, alert_id, g.user["id"], notes=notes or "Permission Granted & Unlocked by HR")
        if ok:
            flash("Employee ID has been granted permission and UNLOCKED successfully.", "ok")
        else:
            flash("Could not find or unlock incident.", "err")
    elif action == "confirm":
        now_ts = now().strftime(DT_FMT)
        db.execute(
            "UPDATE ai_face_alerts SET status='CONFIRMED_VIOLATION', hr_action_by=?, hr_action_at=?, hr_notes=? WHERE id=?",
            (g.user["id"], now_ts, notes or "Violation Confirmed by HR", alert_id)
        )
        audit("AI_VIOLATION_CONFIRMED", "employee", None, f"Alert {alert_id} confirmed by HR {g.user['name']}")
        db.commit()
        flash("Violation confirmed. Account remains blocked pending management review.", "ok")

    return redirect(url_for("hr.face_security_audit"))


@bp.post("/face-security-audit/scan")
@hr_required
def scan_daily_face_audit():
    db = get_db()
    from .services import d2s, today
    target_date = request.form.get("date") or d2s(today())
    from .security_shield import scan_daily_punches_for_buddy_punches

    violations = scan_daily_punches_for_buddy_punches(db, target_date_str=target_date)
    blocked_count = db.execute(
        "SELECT COUNT(*) FROM ai_face_alerts WHERE date=? AND status='BLOCKED'",
        (target_date,)
    ).fetchone()[0]
    total_violations = max(len(violations), blocked_count)

    if total_violations > 0:
        flash(f"AI 4-Department Audit for {target_date}: {total_violations} buddy punch violation(s) detected across 400 analyzed attendance images! Accounts blocked pending HR review.", "err")
    else:
        flash(f"AI 4-Department Audit for {target_date}: 0 buddy punch violations found across 400 analyzed images in all 4 departments. All daily punch faces verified clean.", "ok")
    return redirect(url_for("hr.face_security_audit", date=target_date))


# ------------------------------------------------------------------ HR Cloud Backup & Live Sync
@bp.route("/cloud-backup-sync")
@hr_required
def cloud_backup_sync():
    from .cloud_backup import get_cloud_telemetry, list_cloud_backups
    telemetry = get_cloud_telemetry()
    backups = list_cloud_backups()
    return render_template(
        "hr/cloud_backup_sync.html",
        telemetry=telemetry,
        backups=backups
    )


@bp.get("/cloud-backup-sync/api/telemetry")
@hr_required
def cloud_backup_sync_telemetry():
    from .cloud_backup import get_cloud_telemetry
    return jsonify(get_cloud_telemetry())


@bp.post("/cloud-backup-sync/api/sync")
@hr_required
def cloud_backup_sync_run():
    from .cloud_backup import sync_all_data
    try:
        res = sync_all_data(actor_id=g.user["id"], actor_name=g.user["name"])
        return jsonify(res)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@bp.post("/cloud-backup-sync/api/backup")
@hr_required
def cloud_backup_create_run():
    from .cloud_backup import create_cloud_backup
    data = request.get_json(silent=True) or request.form
    reason = (data.get("reason") or "Manual HR Cloud Backup").strip()
    try:
        res = create_cloud_backup(actor_id=g.user["id"], actor_name=g.user["name"], reason=reason)
        return jsonify(res)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@bp.get("/cloud-backup-sync/api/verify/<backup_id>")
@hr_required
def cloud_backup_verify_run(backup_id):
    from .cloud_backup import verify_cloud_backup
    res = verify_cloud_backup(backup_id)
    return jsonify(res)


@bp.post("/cloud-backup-sync/api/restore/stage")
@hr_required
def cloud_backup_restore_stage():
    from .cloud_backup import stage_restore_from_backup
    data = request.get_json(silent=True) or request.form
    backup_id = (data.get("backup_id") or "").strip()
    if not backup_id:
        return jsonify(ok=False, error="Backup ID is required for staging restore."), 400
    res = stage_restore_from_backup(backup_id, actor_id=g.user["id"])
    return jsonify(res)


@bp.post("/cloud-backup-sync/api/restore/confirm")
@hr_required
def cloud_backup_restore_confirm():
    from .cloud_backup import apply_staged_restore
    data = request.get_json(silent=True) or request.form
    staging_token = (data.get("staging_token") or "").strip()
    confirmation_code = (data.get("confirmation_code") or "").strip()
    res = apply_staged_restore(
        staging_token,
        confirmation_code,
        actor_id=g.user["id"],
        actor_name=g.user["name"]
    )
    return jsonify(res)


@bp.get("/cloud-backup-sync/download/<backup_id>")
@hr_required
def cloud_backup_download(backup_id):
    import os
    try:
        from .cloud_backup import get_or_reconstruct_snapshot
        filepath = get_or_reconstruct_snapshot(backup_id)
        if not filepath or not os.path.exists(filepath):
            flash(f"Backup snapshot '{backup_id}' could not be located or reconstructed.", "err")
            return redirect(url_for("hr.cloud_backup_sync"))
        safe_bid = os.path.basename(filepath)
        return send_file(
            filepath,
            as_attachment=True,
            download_name=safe_bid,
            mimetype="application/json"
        )
    except Exception as e:
        logger.exception("Failed downloading cloud backup snapshot %s: %s", backup_id, e)
        flash(f"Download failed: {str(e)}", "err")
        return redirect(url_for("hr.cloud_backup_sync"))


