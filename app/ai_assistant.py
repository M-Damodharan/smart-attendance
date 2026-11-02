"""AI Attendance Assistant for SBG Smart Attendance System.
Provides intelligent, permission-aware answers for employees and HR administrators.
"""
from datetime import date, datetime, timedelta
import re
from flask import Blueprint, jsonify, request, g
from .auth import login_required
from .db import get_db
from .services import (
    today, now, d2s, parse_dt, format_duration, get_employee_category,
    get_employee_shift_for_date, month_range, build_register, STATUS_LABELS
)

bp = Blueprint("ai", __name__, url_prefix="/api/ai")


def _get_user_today_attendance(user_id: int):
    db = get_db()
    t_str = d2s(today())
    return db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (user_id, t_str)).fetchone()


def _nearest_sunday(ref_date=None):
    if ref_date is None:
        ref_date = today()
    days_ahead = 6 - ref_date.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return ref_date + timedelta(days=days_ahead)


def _get_company_today_stats():
    db = get_db()
    t_str = d2s(today())
    emps = db.execute("SELECT e.*, s.name shift_name FROM employees e LEFT JOIN shifts s ON s.id=e.shift_id WHERE e.active=1 AND e.role='employee'").fetchall()
    reg = build_register(emps, today(), today())

    present = []
    working = []
    absent = []
    late = []
    on_leave = []
    on_od = []
    shift1_present = []
    shift2_present = []

    from .services import resolve_employee_shift

    for e in emps:
        code = reg[e["id"]]["days"][today()]
        rec = reg[e["id"]]["recs"].get(today())
        rec_d = dict(rec) if rec else {}
        eff_shift = resolve_employee_shift(e["id"], today(), rec_d.get("check_in"), db, fallback_shift_id=rec_d.get("shift_id"))

        if code in ("P", "OT", "HD", "IN", "MP"):
            present.append((e, rec, code, eff_shift))
            is_s2 = False
            if eff_shift:
                s_name = (eff_shift["name"] or "").lower()
                s_start = eff_shift["start_time"] or ""
                if "shift 2" in s_name or s_start.startswith("20:"):
                    is_s2 = True
            if is_s2:
                shift2_present.append((e, rec, code, eff_shift))
            else:
                shift1_present.append((e, rec, code, eff_shift))

            if code == "IN":
                working.append((e, rec))
        elif code in ("L", "HL"):
            on_leave.append(e)
        elif code == "OD":
            on_od.append(e)
        else:
            absent.append(e)

        if rec and rec["late"]:
            late.append((e, rec))

    return {
        "total": len(emps),
        "present": present,
        "shift1_present": shift1_present,
        "shift2_present": shift2_present,
        "working": working,
        "absent": absent,
        "late": late,
        "on_leave": on_leave,
        "on_od": on_od,
        "date": today(),
    }


def process_ai_query(user, query: str):
    """Processes natural language query from user and returns a markdown response."""
    from .security_shield import scan_threats, log_security_event

    # -------------------------------------------------------------------------
    # Cyber Security Threat & Prompt Injection Shield
    # -------------------------------------------------------------------------
    is_threat, threat_type, matched = scan_threats(query)
    if is_threat:
        log_security_event(threat_type, f"AI Query: {query[:120]} (matched: {matched})", source="AI_SHIELD", actor_id=user["id"], actor_name=user["name"])
        return {
            "reply": f"🛡️ **Cyber Defense Shield Triggered:** Security violation detected (`{threat_type}`).\n\n"
                     "Unauthorized prompt injection, data exfiltration, or tampering attempts are strictly blocked.\n"
                     "This incident has been permanently logged in the security audit ledger and reported to HR Administration.",
            "type": "security_block",
            "threat_type": threat_type
        }

    q = (query or "").strip().lower()
    role = user["role"]
    uid = user["id"]
    db = get_db()
    t = today()

    # Normalize query for easier pattern matching
    q_clean = re.sub(r"[^\w\s]", "", q)

    # -------------------------------------------------------------------------
    # 1. Company-wide / HR restricted queries
    # -------------------------------------------------------------------------
    is_asking_daily_report = any(k in q_clean for k in ["daily report", "daily attendance report", "generate daily report", "todays report", "executive report today", "attendance report today"])
    is_asking_weekly_report = any(k in q_clean for k in ["weekly report", "weekly attendance", "past 7 days report", "generate weekly report", "last 7 days"])
    is_asking_monthly_report = any(k in q_clean for k in ["monthly report", "monthly attendance report", "generate monthly report", "month report", "full month report"])
    is_asking_missed_punches = any(k in q_clean for k in ["missed punch", "missed punches", "missing punch", "missing punches", "mp list", "missed out punch", "show missed punches"])
    is_asking_security_suspicious = any(k in q_clean for k in ["suspicious", "suspicious activity", "duplicate face", "duplicate attendance", "buddy punch", "face alerts", "security alerts", "breach"])
    is_asking_dept_summary = any(k in q_clean for k in ["department summary", "department attendance", "department wise", "dept summary", "by department"])
    is_asking_sunday_duty = any(k in q_clean for k in ["sunday working", "sunday duty", "sunday shift", "sunday attendance", "assigned to sunday"])
    is_asking_salary_summary = any(k in q_clean for k in ["salary summary", "payroll summary", "payroll status", "monthly salary", "calculate salary"])

    is_asking_present_count = any(k in q_clean for k in ["how many employees are present", "how many are present", "employees present today", "present count", "total present today", "who is present", "present employees", "present list"])
    is_asking_absent_count = any(k in q_clean for k in ["how many employees are absent today", "how many employees are absent", "how many are absent today", "how many are absent", "how many absent", "absent count", "total absent today", "employees absent today"])
    is_asking_od_pending = any(k in q_clean for k in ["show pending od approvals", "pending od approvals", "pending od", "od approvals", "pending on duty", "pending od requests", "show pending od"])
    is_asking_shift1 = any(k in q_clean for k in ["shift 1 present", "shift 1 list", "who is in shift 1", "shift 1 employees", "shift 1"])
    is_asking_shift2 = any(k in q_clean for k in ["shift 2 present", "shift 2 list", "who is in shift 2", "shift 2 employees", "night shift present", "shift 2"])
    is_asking_absent_list = any(k in q_clean for k in ["show todays absent employees", "show absent employees", "who is absent today", "absent employees today", "absent list", "todays absent"])
    is_asking_working_now = any(k in q_clean for k in ["who is working", "currently working", "who punched in", "active employees now"])
    is_asking_late_list = any(k in q_clean for k in ["who is late", "who was late", "late arrivals", "late today"])

    if is_asking_daily_report:
        if role != "hr":
            return {"reply": "🔒 **Access Restricted:** Executive company attendance reports are restricted to HR administrators.", "type": "restricted"}
        stats = _get_company_today_stats()
        tot = stats["total"]
        pres_cnt = len(stats["present"])
        abs_cnt = len(stats["absent"])
        late_cnt = len(stats["late"])
        work_cnt = len(stats["working"])
        pct = round((pres_cnt / tot * 100), 1) if tot > 0 else 0
        
        dept_rows = db.execute("""
            SELECT e.department, COUNT(DISTINCT e.id) as total,
                   COUNT(DISTINCT CASE WHEN a.status IN ('P','OT','HD','IN','MP') THEN a.emp_id END) as present
            FROM employees e
            LEFT JOIN attendance a ON a.emp_id=e.id AND a.date=?
            WHERE e.active=1 AND e.role='employee'
            GROUP BY e.department
        """, (d2s(t),)).fetchall()
        dept_lines = [f"• **{r['department'] or 'General'}:** {r['present']}/{r['total']} present ({round(r['present']/r['total']*100, 1) if r['total'] else 0}%)" for r in dept_rows]
        
        return {
            "reply": f"📑 **Executive Daily Attendance Report — {t.strftime('%A, %d %B %Y')}**\n\n"
                     f"**1. High-Level Metrics:**\n"
                     f"• **Total Active Workforce:** **{tot}** employees\n"
                     f"• **Present & On Duty:** **{pres_cnt}** ({pct}% attendance rate)\n"
                     f"• **Currently Clocked In:** **{work_cnt}** employees\n"
                     f"• **Absent / Unpunched:** **{abs_cnt}** employees\n"
                     f"• **Late Arrivals:** **{late_cnt}** employees\n"
                     f"• **On Approved Leave:** **{len(stats['on_leave'])}** employees\n\n"
                     f"**2. Department Breakdown:**\n" + "\n".join(dept_lines) + "\n\n"
                     f"💡 *Live data synced from 24/7 attendance database.*",
            "type": "hr_report"
        }

    if is_asking_weekly_report:
        if role != "hr":
            return {"reply": "🔒 **Access Restricted:** Weekly reports are restricted to HR administrators.", "type": "restricted"}
        tot_emps = db.execute("SELECT COUNT(*) FROM employees WHERE active=1 AND role='employee'").fetchone()[0]
        past_7 = [t - timedelta(days=i) for i in range(6, -1, -1)]
        d_start, d_end = d2s(past_7[0]), d2s(past_7[-1])
        rows = db.execute("""
            SELECT a.date,
                   COUNT(DISTINCT CASE WHEN a.status IN ('P','OT','HD','IN','MP') THEN a.emp_id END) as pres,
                   COUNT(DISTINCT CASE WHEN a.late=1 THEN a.emp_id END) as lates
            FROM attendance a
            JOIN employees e ON e.id=a.emp_id
            WHERE a.date >= ? AND a.date <= ? AND e.active=1
            GROUP BY a.date
        """, (d_start, d_end)).fetchall()
        row_dict = {r["date"]: r for r in rows}
        
        lines = []
        tot_pres_sum = 0
        for d in past_7:
            ds = d2s(d)
            rec = row_dict.get(ds)
            p = rec["pres"] if rec else 0
            l = rec["lates"] if rec else 0
            ab = max(0, tot_emps - p)
            pct = round(p / tot_emps * 100, 1) if tot_emps else 0
            tot_pres_sum += p
            lines.append(f"| {d.strftime('%a, %d %b')} | {p} | {ab} | {l} | **{pct}%** |")
            
        avg_pct = round(tot_pres_sum / (tot_emps * 7) * 100, 1) if tot_emps else 0
        table_md = "| Date | Present | Absent | Late | Attendance % |\n| :--- | :---: | :---: | :---: | :---: |\n" + "\n".join(lines)
        return {
            "reply": f"📊 **7-Day Weekly Attendance Report ({d_start} to {d_end}):**\n\n"
                     f"• **Total Employees:** {tot_emps}\n"
                     f"• **Average Weekly Attendance:** **{avg_pct}%**\n\n"
                     + table_md + "\n\n"
                     f"💡 *Live metrics generated from historical attendance records.*",
            "type": "hr_report"
        }

    if is_asking_monthly_report:
        if role != "hr":
            return {"reply": "🔒 **Access Restricted:** Monthly reports are restricted to HR administrators.", "type": "restricted"}
        tot_emps = db.execute("SELECT COUNT(*) FROM employees WHERE active=1 AND role='employee'").fetchone()[0]
        start_d, end_d = month_range(t.year, t.month)
        
        stats_row = db.execute("""
            SELECT
                COUNT(DISTINCT a.date) as active_days,
                COUNT(CASE WHEN a.status IN ('P','OT','HD','IN','MP') THEN 1 END) as total_present_punches,
                COUNT(CASE WHEN a.late=1 THEN 1 END) as total_late_punches,
                SUM(a.ot_hours) as total_ot_hours
            FROM attendance a
            JOIN employees e ON e.id=a.emp_id
            WHERE a.date >= ? AND a.date <= ? AND e.active=1
        """, (d2s(start_d), d2s(end_d))).fetchone()
        
        active_days = (stats_row["active_days"] if stats_row else 0) or 1
        tot_pres = (stats_row["total_present_punches"] if stats_row else 0) or 0
        tot_late = (stats_row["total_late_punches"] if stats_row else 0) or 0
        tot_ot = round(float((stats_row["total_ot_hours"] if stats_row else 0) or 0.0), 1)
        avg_pres_day = round(tot_pres / active_days, 1)
        
        leave_sum = db.execute("""
            SELECT SUM(days) as l_days FROM leaves WHERE status='Approved' AND from_date >= ? AND to_date <= ?
        """, (d2s(start_d), d2s(end_d))).fetchone()
        l_days = float((leave_sum["l_days"] if leave_sum else 0) or 0.0)

        return {
            "reply": f"📅 **Executive Monthly Attendance Report — {t.strftime('%B %Y')}:**\n\n"
                     f"• **Total Active Employees:** **{tot_emps}**\n"
                     f"• **Days Recorded This Month:** **{active_days}** days\n"
                     f"• **Average Daily Present:** **{avg_pres_day}** employees/day\n"
                     f"• **Total Working Overtime Logged:** **{tot_ot} hrs**\n"
                     f"• **Total Late Punch Incidents:** **{tot_late}**\n"
                     f"• **Total Approved Leave Days:** **{l_days:g}** days\n\n"
                     f"💡 *Generated from live cloud attendance register.*",
            "type": "hr_report"
        }

    if is_asking_missed_punches:
        if role != "hr":
            return {"reply": "🔒 **Access Restricted:** Missed punch management is restricted to HR administrators.", "type": "restricted"}
        mp_rows = db.execute("""
            SELECT a.*, e.name as emp_name, e.emp_code, e.department
            FROM attendance a
            JOIN employees e ON e.id=a.emp_id
            WHERE a.check_in IS NOT NULL AND (a.status='MP' OR a.check_out IS NULL)
            AND (a.mp_resolved IS NULL OR a.mp_resolved=0)
            ORDER BY a.date DESC, a.check_in DESC LIMIT 15
        """).fetchall()
        if not mp_rows:
            return {"reply": "✨ **No Pending Missed Punches!** All employee punches are complete and resolved.", "type": "hr_mp"}
        lines = [f"{i+1}. **{r['emp_name']}** (`{r['emp_code']}`) — Date: **{r['date']}** | In: **{r['check_in'][11:16]}** | Status: **{r['status']}** ({r['department'] or 'General'})" for i, r in enumerate(mp_rows)]
        return {
            "reply": f"⚠️ **Pending Missed OUT Punches ({len(mp_rows)} records):**\n\n"
                     f"Employees who punched in but did not punch out within 12 hours:\n\n" + "\n".join(lines) + "\n\n"
                     f"👉 *Review or approve in [Missed Punches List](/hr/missed-punches).*",
            "type": "hr_mp"
        }

    if is_asking_security_suspicious:
        if role != "hr":
            return {"reply": "🔒 **Access Restricted:** Security audit alerts are restricted to HR administrators.", "type": "restricted"}
        alerts = db.execute("""
            SELECT a.*, e1.name as emp_name, e1.emp_code, e2.name as target_name, e2.emp_code as target_code
            FROM ai_face_alerts a
            JOIN employees e1 ON e1.id=a.emp_id
            LEFT JOIN employees e2 ON e2.id=a.target_emp_id
            WHERE a.status='BLOCKED'
            ORDER BY a.id DESC LIMIT 5
        """).fetchall()
        if not alerts:
            return {"reply": "🛡️ **Security Status Normal:** No active biometric duplicate or buddy-punching alerts detected.", "type": "hr_security"}
        lines = [f"• 🚨 **{a['emp_name']}** (`{a['emp_code']}`) matched with **{a['target_name'] or 'Unknown'}** (`{a['target_code'] or 'ID'}`) — **{round(a['similarity']*100, 1)}% face similarity** on {a['date']}" for a in alerts]
        return {
            "reply": f"🚨 **AI Security & Duplicate Face Alerts ({len(alerts)} Active):**\n\n" + "\n".join(lines) + "\n\n"
                     f"👉 *Review in [AI Face Security Audit](/hr/face-security-audit).*",
            "type": "hr_security"
        }

    if is_asking_dept_summary:
        if role != "hr":
            return {"reply": "🔒 **Access Restricted:** Departmental summaries are restricted to HR administrators.", "type": "restricted"}
        rows = db.execute("""
            SELECT e.department,
                   COUNT(DISTINCT e.id) as total,
                   COUNT(DISTINCT CASE WHEN a.status IN ('P','OT','HD','IN','MP') THEN a.emp_id END) as present,
                   COUNT(DISTINCT CASE WHEN a.late=1 THEN a.emp_id END) as late
            FROM employees e
            LEFT JOIN attendance a ON a.emp_id=e.id AND a.date=?
            WHERE e.active=1 AND e.role='employee'
            GROUP BY e.department
        """, (d2s(t),)).fetchall()
        lines = []
        for r in rows:
            tot = r["total"]
            p = r["present"]
            ab = max(0, tot - p)
            pct = round(p / tot * 100, 1) if tot else 0
            lines.append(f"| {r['department'] or 'General'} | {tot} | {p} | {ab} | {r['late']} | **{pct}%** |")
        table_md = "| Department | Total Staff | Present | Absent | Late | Attendance % |\n| :--- | :---: | :---: | :---: | :---: | :---: |\n" + "\n".join(lines)
        return {
            "reply": f"🏢 **Department-Wise Attendance Breakdown ({t.strftime('%d %b %Y')}):**\n\n" + table_md,
            "type": "hr_dept"
        }

    if is_asking_sunday_duty:
        if role != "hr":
            sw_my = db.execute("SELECT * FROM sunday_working WHERE emp_id=? ORDER BY sunday_date DESC LIMIT 3", (uid,)).fetchall()
            if not sw_my:
                return {"reply": "You do not have any Sunday shifts assigned currently.", "type": "sunday"}
            lines = [f"• **{r['sunday_date']}:** {r['duty_type']} ({r['status']})" for r in sw_my]
            return {"reply": "📅 **Your Sunday Duty Schedule:**\n\n" + "\n".join(lines), "type": "sunday"}
        
        sun_rows = db.execute("""
            SELECT sw.*, e.name as emp_name, e.emp_code, e.department
            FROM sunday_working sw
            JOIN employees e ON e.id=sw.emp_id
            WHERE sw.sunday_date >= date('now', '-7 days')
            ORDER BY sw.sunday_date DESC, e.emp_code
        """).fetchall()
        if not sun_rows:
            return {"reply": f"📅 **Sunday Working Management:** No employees are currently scheduled for Sunday shifts. You can assign them in the [Sunday Working Module](/hr/sunday-working).", "type": "hr_sunday"}
        lines = [f"• **{r['emp_name']}** (`{r['emp_code']}`) — {r['sunday_date']} | {r['duty_type']} | Status: **{r['status']}**" for r in sun_rows[:10]]
        return {
            "reply": f"📅 **Sunday Working Duty Summary ({len(sun_rows)} Scheduled):**\n\n" + "\n".join(lines) + "\n\n"
                     f"👉 *Manage in [Sunday Working Module](/hr/sunday-working).*",
            "type": "hr_sunday"
        }

    if is_asking_salary_summary:
        if role != "hr":
            pay = db.execute("SELECT * FROM monthly_payroll WHERE emp_id=? ORDER BY month DESC LIMIT 1", (uid,)).fetchone()
            if not pay:
                return {"reply": "No salary record generated for your account yet.", "type": "salary"}
            return {
                "reply": f"💳 **Your Latest Salary Slip ({pay['month']}):**\n\n"
                         f"• **Gross Salary:** ₹{pay['gross_salary']:,.2f}\n"
                         f"• **Net Payable:** **₹{pay['net_salary']:,.2f}**\n"
                         f"• **Status:** {pay['status']}\n\n"
                         f"👉 *View breakdown in [My Salary](/my-salary).*",
                "type": "salary"
            }
        
        latest_month = db.execute("SELECT month FROM monthly_payroll ORDER BY month DESC LIMIT 1").fetchone()
        if not latest_month:
            return {"reply": "No monthly payroll records calculated yet. Calculate payroll in [HR Payroll Module](/hr/payroll).", "type": "hr_payroll"}
        m = latest_month["month"]
        pays = db.execute("SELECT COUNT(*) as count, SUM(net_salary) as tot_net, SUM(gross_salary) as tot_gross FROM monthly_payroll WHERE month=?", (m,)).fetchone()
        return {
            "reply": f"💳 **Payroll Summary for {m}:**\n\n"
                     f"• **Employees Processed:** {pays['count']}\n"
                     f"• **Total Gross Salary:** ₹{float(pays['tot_gross'] or 0):,.2f}\n"
                     f"• **Total Net Payable:** **₹{float(pays['tot_net'] or 0):,.2f}**\n\n"
                     f"👉 *Manage in [HR Payroll Management](/hr/payroll).*",
            "type": "hr_payroll"
        }

    if is_asking_shift1:
        if role != "hr":
            return {"reply": "🔒 **Access Restricted:** Shift breakdown reports are restricted to HR administrators.", "type": "restricted"}
        stats = _get_company_today_stats()
        s1_emps = stats["shift1_present"]
        if not s1_emps:
            return {"reply": f"☀️ **Shift 1 (08:00 AM – 08:00 PM):** No employees have punched in under Shift 1 today ({t.strftime('%d %b %Y')}).", "type": "hr_shift"}
        lines = [f"{i+1}. **{e['name']}** (`{e['emp_code']}`) — In at **{rec['check_in'][11:16]}** ({rec['status'] or 'Working'})" for i, (e, rec, code, eff_s) in enumerate(s1_emps)]
        return {
            "reply": f"☀️ **Shift 1 (08:00 AM – 08:00 PM) Present List — {len(s1_emps)} Total:**\n\n" + "\n".join(lines),
            "type": "hr_shift"
        }

    if is_asking_shift2:
        if role != "hr":
            return {"reply": "🔒 **Access Restricted:** Shift breakdown reports are restricted to HR administrators.", "type": "restricted"}
        stats = _get_company_today_stats()
        s2_emps = stats["shift2_present"]
        if not s2_emps:
            return {"reply": f"🌙 **Shift 2 (08:00 PM – 08:00 AM):** No employees have punched in under Shift 2 today ({t.strftime('%d %b %Y')}).", "type": "hr_shift"}
        lines = [f"{i+1}. **{e['name']}** (`{e['emp_code']}`) — In at **{rec['check_in'][11:16]}** ({rec['status'] or 'Working'})" for i, (e, rec, code, eff_s) in enumerate(s2_emps)]
        return {
            "reply": f"🌙 **Shift 2 (08:00 PM – 08:00 AM) Present List — {len(s2_emps)} Total:**\n\n" + "\n".join(lines),
            "type": "hr_shift"
        }

    if is_asking_present_count:
        if role != "hr":
            return {
                "reply": "🔒 **Access Restricted:** You only have permission to view your personal attendance details. Company-wide presence statistics are only accessible to HR administrators.",
                "type": "restricted"
            }
        stats = _get_company_today_stats()
        tot = stats["total"]
        pres_cnt = len(stats["present"])
        s1_cnt = len(stats["shift1_present"])
        s2_cnt = len(stats["shift2_present"])
        work_cnt = len(stats["working"])
        abs_cnt = len(stats["absent"])
        pct = round((pres_cnt / tot * 100), 1) if tot > 0 else 0
        return {
            "reply": f"📊 **Today's Attendance Overview ({t.strftime('%A, %d %b %Y')}):**\n\n"
                     f"• **Total Present:** **{pres_cnt}** of **{tot}** employees ({pct}% attendance)\n"
                     f"  - ☀️ **Shift 1 (08:00 AM – 08:00 PM):** **{s1_cnt}** Present\n"
                     f"  - 🌙 **Shift 2 (08:00 PM – 08:00 AM):** **{s2_cnt}** Present\n"
                     f"• **Currently Working:** **{work_cnt}** employees punched in\n"
                     f"• **Absent / Not Checked In:** **{abs_cnt}** employees\n"
                     f"• **On Approved Leave:** **{len(stats['on_leave'])}** employees\n"
                     f"• **On Duty (OD):** **{len(stats['on_od'])}** employees\n"
                     f"• **Late Arrivals:** **{len(stats['late'])}** employees",
            "type": "hr_stats"
        }

    if is_asking_absent_count:
        if role != "hr":
            return {
                "reply": "🔒 **Access Restricted:** Company absence figures are restricted to HR administrators.",
                "type": "restricted"
            }
        stats = _get_company_today_stats()
        abs_cnt = len(stats["absent"])
        tot = stats["total"]
        return {
            "reply": f"📋 **Today's Absence Count ({t.strftime('%A, %d %b %Y')}):**\n\n"
                     f"• **Absent / Not Checked In:** **{abs_cnt}** of **{tot}** scheduled employees\n"
                     f"• **Present Today:** **{len(stats['present'])}** employees\n"
                     f"• **On Approved Leave:** **{len(stats['on_leave'])}** employees\n\n"
                     f"💡 *Ask “Show today's absent employees” to see the full named list.*",
            "type": "hr_absent_count"
        }

    if is_asking_od_pending:
        if role != "hr":
            return {
                "reply": "🔒 **Access Restricted:** On-Duty approval requests are restricted to HR administrators.",
                "type": "restricted"
            }
        pending_ods = db.execute(
            "SELECT od.*, e.name emp_name, e.emp_code "
            "FROM od_requests od "
            "JOIN employees e ON e.id=od.emp_id "
            "WHERE od.status='Pending' "
            "ORDER BY od.created_at ASC"
        ).fetchall()
        if not pending_ods:
            return {
                "reply": "🎉 **No Pending OD Requests:** All employee On-Duty requests have been reviewed and approved or rejected.",
                "type": "hr_od"
            }
        lines = [f"{i+1}. **{od['emp_name']}** (`{od['emp_code']}`) — Date: **{od['od_date']}** | Loc: *{od['location']}* | Reason: {od['purpose'] or 'Official'}" for i, od in enumerate(pending_ods)]
        return {
            "reply": f"⏳ **Pending On-Duty (OD) Requests ({len(pending_ods)} Total):**\n\n" + "\n".join(lines),
            "type": "hr_od"
        }

    if is_asking_absent_list:
        if role != "hr":
            return {
                "reply": "🔒 **Access Restricted:** Employee lists and company absence records are restricted to HR administrators.",
                "type": "restricted"
            }
        stats = _get_company_today_stats()
        abs_emps = stats["absent"]
        if not abs_emps:
            return {
                "reply": f"🎉 **100% Attendance!** There are **no absent employees** today ({t.strftime('%d %b %Y')}).",
                "type": "hr_absent"
            }
        lines = [f"{i+1}. **{e['name']}** (`{e['emp_code']}`) — {e['department'] or 'General'} · {e['shift_name'] or 'Shift 1'}" for i, e in enumerate(abs_emps)]
        return {
            "reply": f"📋 **Today's Absent Employees ({t.strftime('%d %b %Y')}) — {len(abs_emps)} Total:**\n\n" + "\n".join(lines),
            "type": "hr_absent"
        }

    if is_asking_working_now:
        if role != "hr":
            return {
                "reply": "🔒 **Access Restricted:** Active staff presence reports are restricted to HR administrators.",
                "type": "restricted"
            }
        stats = _get_company_today_stats()
        working = stats["working"]
        if not working:
            return {"reply": "Currently, no employees have an active in-progress punch.", "type": "hr_working"}
        lines = [f"• **{e['name']}** (`{e['emp_code']}`) — In at **{rec['check_in'][11:16]}** ({e['department'] or 'Staff'})" for e, rec in working]
        return {
            "reply": f"⏱️ **Currently Working ({len(working)} employees):**\n\n" + "\n".join(lines),
            "type": "hr_working"
        }

    if is_asking_late_list:
        if role != "hr":
            return {
                "reply": "🔒 **Access Restricted:** Staff punctuality and late arrival reports are restricted to HR administrators.",
                "type": "restricted"
            }
        stats = _get_company_today_stats()
        lates = stats["late"]
        if not lates:
            return {"reply": f"✨ **All employees arrived on time today ({t.strftime('%d %b %Y')})!**", "type": "hr_late"}
        lines = [f"• **{e['name']}** (`{e['emp_code']}`) — In at **{rec['check_in'][11:16]}**" for e, rec in lates]
        return {
            "reply": f"⚠️ **Late Arrivals Today ({len(lates)}):**\n\n" + "\n".join(lines),
            "type": "hr_late"
        }

    # -------------------------------------------------------------------------
    # 2. Personal queries (Employee or HR for self)
    # -------------------------------------------------------------------------
    raw_rec = _get_user_today_attendance(uid)
    raw_shift = get_employee_shift_for_date(uid, t, user.get("shift_id") if isinstance(user, dict) else user["shift_id"])
    raw_cat = get_employee_category(uid)

    rec = dict(raw_rec) if raw_rec else None
    shift = dict(raw_shift) if raw_shift else None
    category = dict(raw_cat) if raw_cat else None

    # 2A. "How many hours have I worked today?"
    if any(k in q_clean for k in ["how many hours have i worked", "hours worked today", "how much time i worked", "how many hours today", "working hours today", "my hours"]):
        if not rec or not rec["check_in"]:
            return {
                "reply": f"You have **not punched in yet today** ({t.strftime('%A, %d %b')}). Please punch in on the Punch screen when you begin your shift.",
                "type": "hours"
            }
        if rec["check_out"]:
            dur_str = format_duration(rec["work_hours"])
            in_t = parse_dt(rec["check_in"]).strftime("%I:%M %p")
            out_t = parse_dt(rec["check_out"]).strftime("%I:%M %p")
            ot_info = f" (including **{format_duration(rec['ot_hours'])}** overtime)" if rec.get("ot_hours") and rec["ot_hours"] > 0 else ""
            return {
                "reply": f"⏱️ **Today's Working Hours:**\n\nYou worked **{dur_str}** today{ot_info}.\n\n• **Punch In:** {in_t}\n• **Punch Out:** {out_t}\n• **Status:** **{STATUS_LABELS.get(rec['status'], rec['status'])}**",
                "type": "hours"
            }
        else:
            in_dt = parse_dt(rec["check_in"])
            in_t = in_dt.strftime("%I:%M %p")
            cur_sec = max((now() - in_dt).total_seconds(), 0)
            cur_hrs = cur_sec / 3600.0
            cur_dur = format_duration(cur_hrs)
            req_h = category.get("work_hours", 10.0)
            return {
                "reply": f"🟢 **Shift in Progress:**\n\nYou checked in at **{in_t}** and have worked **{cur_dur}** so far today.\n\n• **Target Working Hours:** {int(req_h)} hours ({category['name']})\n• **Assigned Shift:** {shift['name']} ({shift['start_time']} – {shift['end_time']})",
                "type": "hours"
            }

    # 2B. "What time did I punch in and punch out?"
    if any(k in q_clean for k in ["what time did i punch in", "what time punch out", "my punch times", "when did i punch", "punch in and punch out", "check in time", "check out time"]):
        if not rec or not rec["check_in"]:
            return {
                "reply": f"You have not punched in or out today ({t.strftime('%d %b %Y')}).",
                "type": "punch_times"
            }
        in_t = parse_dt(rec["check_in"]).strftime("%I:%M %p")
        if rec["check_out"]:
            out_t = parse_dt(rec["check_out"]).strftime("%I:%M %p")
            dur = format_duration(rec["work_hours"])
            return {
                "reply": f"🕒 **Your Punch Details for Today:**\n\n• **Punch In:** **{in_t}**" + (" (Late)" if rec["late"] else " (On Time)") + f"\n• **Punch Out:** **{out_t}**\n• **Total Worked Duration:** **{dur}**\n• **Attendance Status:** **{STATUS_LABELS.get(rec['status'], rec['status'])}**",
                "type": "punch_times"
            }
        else:
            return {
                "reply": f"🕒 **Your Punch Details for Today:**\n\n• **Punch In:** **{in_t}**" + (" (Late)" if rec["late"] else " (On Time)") + "\n• **Punch Out:** *Still working — not checked out yet*",
                "type": "punch_times"
            }

    # 2C. "What is my attendance status?" / "Am I marked Present, Half Day or Absent today?"
    if any(k in q_clean for k in ["what is my attendance status", "my attendance status", "attendance status", "marked present half day or absent", "am i marked present", "am i present today", "am i absent today", "am i half day", "what is my status today", "my status today", "today status"]):
        if not rec or not rec["check_in"]:
            cur_hm = now().strftime("%H:%M")
            is_past_end = cur_hm > shift["end_time"] and shift["end_time"] > shift["start_time"]
            if is_past_end:
                return {
                    "reply": f"❌ You are marked **Absent** today because your scheduled shift ({shift['start_time']}–{shift['end_time']}) has ended and no punch was recorded.",
                    "type": "status"
                }
            return {
                "reply": f"⚠️ You are currently **Not Checked In** for today. Please punch in to be marked Present.",
                "type": "status"
            }
        if rec["check_out"]:
            st = rec["status"] or "P"
            label = STATUS_LABELS.get(st, st)
            exp = ""
            if st in ("P", "OT"):
                exp = f"Full Day Present counted (Clocked out after policy threshold with {format_duration(rec['work_hours'])})."
            elif st == "HD":
                exp = "Half Day counted (Completed between 2:00 PM and 4:00 PM)."
            elif st == "A":
                exp = "Marked Absent because working duration was less than half-day threshold."
            return {
                "reply": f"📋 **Today's Status: {label}** ({st})\n\n{exp}\n\n• **Hours Worked:** {format_duration(rec['work_hours'])}\n• **Punch Times:** {parse_dt(rec['check_in']).strftime('%I:%M %p')} → {parse_dt(rec['check_out']).strftime('%I:%M %p')}",
                "type": "status"
            }
        else:
            return {
                "reply": f"🟢 **Today's Status: Working (In Progress)**\n\nYou punched in at **{parse_dt(rec['check_in']).strftime('%I:%M %p')}**. When you clock out after 4:00 PM (or complete your shift), you will be marked **Full Day Present**.",
                "type": "status"
            }

    # 2D. "How much overtime did I work today?"
    if any(k in q_clean for k in ["how much overtime", "overtime did i work", "overtime today", "my ot today", "ot hours today", "did i work overtime"]):
        if not rec or not rec["check_in"] or not rec["check_out"]:
            return {
                "reply": "You don't have completed overtime recorded for today yet. Overtime is calculated once you check out after completing your category's regular working hours.",
                "type": "overtime"
            }
        ot = rec.get("ot_hours", 0.0) or 0.0
        req_h = category.get("work_hours", 10.0)
        if ot > 0:
            return {
                "reply": f"⭐ **Overtime Today:**\n\nYou worked **{format_duration(ot)}** ({ot:.2f} hours) of overtime today!\n\n• **Regular Category Hours:** {int(req_h)} hours ({category['name']})\n• **Total Worked Today:** {format_duration(rec['work_hours'])}",
                "type": "overtime"
            }
        else:
            return {
                "reply": f"You worked **0 hours of overtime** today.\n\n• **Regular Hours Required:** {int(req_h)} hours for your category (**{category['name']}**).\n• **Hours Worked:** {format_duration(rec['work_hours'])}.",
                "type": "overtime"
            }

    # 2E. "Show my attendance for this month."
    if any(k in q_clean for k in ["show my attendance for this month", "show my attendance this month", "my attendance for this month", "attendance history for this month", "show my attendance history", "attendance this month", "monthly history", "my history this month", "month summary"]):
        start, end = month_range(t.year, t.month)
        emp_obj = db.execute("SELECT * FROM employees WHERE id=?", (uid,)).fetchone() or user
        reg = build_register([emp_obj], start, end)[uid]
        summ = reg["summary"]
        p_ot = summ["P"] + summ["OT"]
        return {
            "reply": f"📅 **Your Attendance Summary for {t.strftime('%B %Y')}:**\n\n"
                     f"• **Present Days:** **{p_ot}** (Full Day: {summ['P']}, Overtime Days: {summ['OT']})\n"
                     f"• **Half Days:** **{summ['HD']}**\n"
                     f"• **Absent Days:** **{summ['A']}**\n"
                     f"• **Approved Leaves:** **{summ['L'] + summ['HL']}**\n"
                     f"• **On Duty (OD):** **{summ['OD']}**\n"
                     f"• **Total Hours Worked:** **{summ['hours']} hrs**\n"
                     f"• **Total Overtime:** **{summ['ot_hours']} hrs**\n"
                     f"• **Late Arrivals:** **{summ['late']}**\n\n"
                     f"💡 *You can view your detailed day-by-day attendance log in the History tab.*",
            "type": "monthly_history"
        }

    # 2F. Shift & Schedule queries
    if any(k in q_clean for k in ["what is my current shift", "my current shift", "current shift", "what is my shift", "my shift timings", "my schedule", "shift schedule", "shift change", "working schedule", "which shift", "my shift"]):
        # Check weekly schedule
        sched = db.execute(
            "SELECT w.*, s.name shift_name, s.start_time, s.end_time FROM weekly_shift_schedules w "
            "JOIN shifts s ON s.id=w.shift_id WHERE w.emp_id=? AND w.start_date<=? AND w.end_date>=? ORDER BY w.id DESC LIMIT 1",
            (uid, d2s(t), d2s(t))
        ).fetchone()
        schedule_note = f" (Special weekly schedule from {sched['start_date']} to {sched['end_date']})" if sched else ""
        
        punch_note = ""
        if rec and rec.get("check_in"):
            from .services import resolve_employee_shift
            actual_shift = resolve_employee_shift(uid, t, rec["check_in"], db, fallback_shift_id=rec.get("shift_id"))
            punch_note = f"\n\n🎯 **Today's Punch Shift:** Based on your check-in at **{parse_dt(rec['check_in']).strftime('%I:%M %p')}**, you are recorded under **{actual_shift['name']} ({actual_shift['start_time']}–{actual_shift['end_time']})**."

        return {
            "reply": f"🕒 **Your Shift & Category Details:**\n\n"
                     f"• **Assigned Shift:** **{shift['name']}** ({shift['start_time']} to {shift['end_time']}){schedule_note}\n"
                     f"• **Grace Period:** {shift['grace_min']} minutes\n"
                     f"• **Employee Category:** **{category['name']}**\n"
                     f"• **Standard Working Hours:** {category['work_hours']:.0f} hours/day" + (f" + {category['overtime_hours']:.0f}h OT" if category.get('overtime_hours') else "")
                     + punch_note,
            "type": "schedule"
        }

    # 2G. Leave Balances
    if any(k in q_clean for k in ["leave balance", "my leaves", "how many leaves", "casual leave", "sick leave"]):
        from .services import leave_balance
        bals = leave_balance(uid, t.year)
        lines = [f"• **{b['name']} ({b['code']}):** Quota: {b['quota']:g} | Used: {b['used']:g} | Balance: **{b['balance']:g}** days" for b in bals]
        return {
            "reply": f"🏖️ **Your Leave Balances ({t.year}):**\n\n" + "\n".join(lines),
            "type": "leaves"
        }

    # 2H. Company Attendance Rules & Policies
    if any(k in q_clean for k in ["rules", "policy", "attendance rules", "half day rule", "full day rule", "working hours rule"]):
        return {
            "reply": "📌 **SBG Smart Attendance Rules & Policy:**\n\n"
                     "• **Half Day:** 8:00 AM to 2:00 PM (14:00).\n"
                     "• **Full Day:** 8:00 AM to 8:00 PM (20:00).\n"
                     "• **Early Full Day Cutoff:** If an employee clocks out after **4:00 PM (16:00)** having completed required hours, the day counts as **Full Day Present**.\n"
                     "• **Category Hours:**\n"
                     "   - **Staff:** 10 working hours/day.\n"
                     "   - **Employees:** 12 working hours/day.\n"
                     "   - **Contract Employees:** 10 regular hours + 2 hours overtime.\n"
                     "• **One Record Per Day:** Exactly one valid check-in and check-out per day is permitted.\n"
                     "• **Unpunched Days:** If no punch is recorded for a scheduled working day, employee is marked **Absent**.",
            "type": "rules"
        }

    # 2I. General Greeting / Help
    name = user["name"].split()[0]
    return {
        "reply": f"Hello {name}! 👋 I am your **SBG Smart Attendance AI Assistant**.\n\n"
                 f"Here are questions you can ask me:\n"
                 f"• *“How many hours have I worked today?”*\n"
                 f"• *“What is my attendance status?”*\n"
                 f"• *“What time did I punch in and punch out?”*\n"
                 f"• *“Show my attendance for this month.”*\n"
                 f"• *“What is my current shift?”*\n"
                 f"• *“How much overtime did I work today?”*\n"
                 + (f"• *“How many employees are absent today?”* *(HR only)*\n• *“Show pending OD approvals.”* *(HR only)*\n• *“How many employees are present today?”* *(HR only)*\n" if role == "hr" else ""),
        "type": "help"
    }


@bp.post("/chat")
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify(ok=False, error="Please type a message."), 400
    from .security_shield import redact_sensitive_data
    res = process_ai_query(g.user, message)
    sanitized = redact_sensitive_data(res)
    return jsonify(ok=True, **sanitized)
