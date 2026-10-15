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

    for e in emps:
        code = reg[e["id"]]["days"][today()]
        rec = reg[e["id"]]["recs"].get(today())
        if code in ("P", "OT", "HD", "IN", "MP"):
            present.append((e, rec, code))
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
    is_asking_present_count = any(k in q_clean for k in ["how many employees are present", "how many are present", "employees present today", "present count", "total present today"])
    is_asking_absent_list = any(k in q_clean for k in ["show todays absent employees", "show absent employees", "who is absent today", "absent employees today", "absent list", "todays absent"])
    is_asking_working_now = any(k in q_clean for k in ["who is working", "currently working", "who punched in", "active employees now"])
    is_asking_late_list = any(k in q_clean for k in ["who is late", "who was late", "late arrivals", "late today"])

    if is_asking_present_count:
        if role != "hr":
            return {
                "reply": "🔒 **Access Restricted:** You only have permission to view your personal attendance details. Company-wide presence statistics are only accessible to HR administrators.",
                "type": "restricted"
            }
        stats = _get_company_today_stats()
        tot = stats["total"]
        pres_cnt = len(stats["present"])
        work_cnt = len(stats["working"])
        abs_cnt = len(stats["absent"])
        pct = round((pres_cnt / tot * 100), 1) if tot > 0 else 0
        return {
            "reply": f"📊 **Today's Attendance Overview ({t.strftime('%A, %d %b %Y')}):**\n\n"
                     f"• **Present:** **{pres_cnt}** of **{tot}** employees ({pct}% attendance)\n"
                     f"• **Currently Working:** **{work_cnt}** employees punched in\n"
                     f"• **Absent / Not Checked In:** **{abs_cnt}** employees\n"
                     f"• **On Approved Leave:** **{len(stats['on_leave'])}** employees\n"
                     f"• **On Duty (OD):** **{len(stats['on_od'])}** employees\n"
                     f"• **Late Arrivals:** **{len(stats['late'])}** employees",
            "type": "hr_stats"
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

    # 2C. "Am I marked Present, Half Day or Absent today?"
    if any(k in q_clean for k in ["marked present half day or absent", "am i marked present", "am i present today", "am i absent today", "am i half day", "what is my status today", "my status today", "today status"]):
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

    # 2E. "Show my attendance history for this month."
    if any(k in q_clean for k in ["attendance history for this month", "show my attendance history", "attendance this month", "monthly history", "my history this month", "month summary"]):
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
    if any(k in q_clean for k in ["what is my shift", "my shift timings", "my schedule", "shift schedule", "shift change", "working schedule"]):
        # Check weekly schedule
        sched = db.execute(
            "SELECT w.*, s.name shift_name, s.start_time, s.end_time FROM weekly_shift_schedules w "
            "JOIN shifts s ON s.id=w.shift_id WHERE w.emp_id=? AND w.start_date<=? AND w.end_date>=? ORDER BY w.id DESC LIMIT 1",
            (uid, d2s(t), d2s(t))
        ).fetchone()
        schedule_note = f" (Special weekly schedule from {sched['start_date']} to {sched['end_date']})" if sched else ""
        return {
            "reply": f"🕒 **Your Shift & Category Details:**\n\n"
                     f"• **Active Shift:** **{shift['name']}**{schedule_note}\n"
                     f"• **Timings:** **{shift['start_time']} to {shift['end_time']}**\n"
                     f"• **Grace Period:** {shift['grace_min']} minutes\n"
                     f"• **Employee Category:** **{category['name']}**\n"
                     f"• **Standard Working Hours:** {category['work_hours']:.0f} hours/day" + (f" + {category['overtime_hours']:.0f}h OT" if category.get('overtime_hours') else ""),
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
                 f"• *“What time did I punch in and punch out?”*\n"
                 f"• *“Am I marked Present, Half Day or Absent today?”*\n"
                 f"• *“How much overtime did I work today?”*\n"
                 f"• *“Show my attendance history for this month.”*\n"
                 f"• *“What is my assigned shift?”*\n"
                 + (f"• *“How many employees are present today?”* *(HR only)*\n• *“Show today's absent employees.”* *(HR only)*\n" if role == "hr" else ""),
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
