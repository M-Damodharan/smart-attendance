"""Business rules: geofence, status calculation, register building, audit log."""
import calendar
import math
import time
import threading
from datetime import date, datetime, timedelta, timezone

from flask import current_app, request, session

from .db import get_db

DATE_FMT = "%Y-%m-%d"
DT_FMT = "%Y-%m-%d %H:%M:%S"

# Indian Standard Time (UTC+5:30) for Sri Balaji Gears
IST = timezone(timedelta(hours=5, minutes=30))

STATUS_LABELS = {
    "P": "Present", "HD": "Half Day", "A": "Absent", "OT": "Overtime",
    "L": "Leave", "HL": "Half-day Leave", "OD": "On Duty", "H": "Holiday",
    "WO": "Weekly Off", "MP": "Missed Punch", "IN": "Working", "-": "",
}


# ------------------------------------------------------------------ helpers
def today() -> date:
    """Current local company date in IST."""
    return datetime.now(IST).date()


def now() -> datetime:
    """Current local company time in IST (naive datetime for clean DB storage)."""
    return datetime.now(IST).replace(microsecond=0, tzinfo=None)


def d2s(d: date) -> str:
    return d.strftime(DATE_FMT)


def s2d(s: str) -> date:
    return datetime.strptime(s, DATE_FMT).date()


def parse_dt(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    s = str(s).strip()
    for fmt in (DT_FMT, "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            val = datetime.strptime(s, fmt)
            if fmt in ("%H:%M:%S", "%H:%M"):
                t = today()
                return val.replace(year=t.year, month=t.month, day=t.day)
            return val
        except ValueError:
            pass
    return None


def format_duration(hours: float) -> str:
    """Format decimal hours into human-readable duration: e.g. '1m', '45m', '8h 15m'."""
    if hours is None or hours <= 0:
        return "0m"
    total_minutes = int(round(hours * 60))
    if total_minutes < 1:
        return "1m" if hours > 0 else "0m"
    if total_minutes < 60:
        return f"{total_minutes}m"
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m:02d}m" if m > 0 else f"{h}h"


def month_range(year: int, month: int):
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def haversine(lat1, lon1, lat2, lon2) -> float:
    """Distance in metres between two GPS points."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


import concurrent.futures
_AUDIT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="audit_worker")


def _bg_insert_audit(now_ts, uid, name, action, entity, entity_id_str, details, ip, turso_url, turso_token, db_file):
    try:
        if turso_url and turso_token:
            from .turso_db import connect_turso
            con = connect_turso(turso_url, turso_token)
        else:
            import sqlite3
            con = sqlite3.connect(db_file)
        con.execute(
            "INSERT INTO audit_log (ts, actor_id, actor_name, action, entity, entity_id, details, ip) VALUES (?,?,?,?,?,?,?,?)",
            (now_ts, uid, name, action, entity, entity_id_str, details, ip),
        )
        if not (turso_url and turso_token):
            con.commit()
            con.close()
    except Exception:
        pass


def audit(action, entity=None, entity_id=None, details=None, actor=None):
    from flask import has_request_context
    uid = None
    name = None
    ip = None
    if actor:
        uid = actor.get("id") if isinstance(actor, dict) else getattr(actor, "id", None)
        name = actor.get("name") if isinstance(actor, dict) else getattr(actor, "name", None)
    elif has_request_context():
        try:
            uid = session.get("uid")
            name = session.get("name")
            ip = request.headers.get("X-Forwarded-For", request.remote_addr) if request else None
        except Exception:
            pass
    now_ts = now().strftime(DT_FMT)
    eid_str = str(entity_id) if entity_id is not None else None

    # In testing environment, run synchronously for instant test assertions
    if current_app.config.get("TESTING"):
        db = get_db()
        db.execute(
            "INSERT INTO audit_log (ts, actor_id, actor_name, action, entity, entity_id, details, ip) VALUES (?,?,?,?,?,?,?,?)",
            (now_ts, uid, name, action, entity, eid_str, details, ip),
        )
        return

    # In production, dispatch non-blocking audit write in background thread pool to eliminate 200ms latency
    turso_url = current_app.config.get("TURSO_DATABASE_URL") if current_app.config.get("USE_TURSO") else None
    turso_token = current_app.config.get("TURSO_AUTH_TOKEN") if current_app.config.get("USE_TURSO") else None
    db_file = current_app.config.get("DATABASE")
    _AUDIT_EXECUTOR.submit(_bg_insert_audit, now_ts, uid, name, action, entity, eid_str, details, ip, turso_url, turso_token, db_file)


def create_notification(title: str, message: str, kind: str = "info", user_id: int = None, target_role: str = None, link_url: str = None, db=None):
    """
    Creates a notification for a specific employee (user_id) or an entire role (target_role='hr' or 'employee').
    """
    if db is None:
        db = get_db()
    cur_time = db.execute("SELECT datetime('now', '+5 hours', '+30 minutes')").fetchone()[0]
    cursor = db.execute(
        "INSERT INTO notifications (user_id, target_role, title, message, kind, status, link_url, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'unread', ?, ?)",
        (user_id, target_role, title, message, kind, link_url, cur_time)
    )
    return cursor.lastrowid


# ------------------------------------------------------------------ employee categories & shift schedules
def get_employee_shift_for_date(emp_id: int, d, fallback_shift_id: int = None, db=None):
    """Retrieve the effective shift for an employee on a given date, supporting weekly schedules."""
    db = db or get_db()
    d_str = d2s(d) if isinstance(d, date) else str(d)[:10]
    row = db.execute(
        "SELECT s.* FROM weekly_shift_schedules w JOIN shifts s ON s.id = w.shift_id "
        "WHERE w.emp_id = ? AND w.start_date <= ? AND w.end_date >= ? "
        "ORDER BY w.id DESC LIMIT 1",
        (emp_id, d_str, d_str),
    ).fetchone()
    if row:
        return row
    all_shifts = get_all_shifts_cached(db)
    if fallback_shift_id:
        for s in all_shifts:
            if s["id"] == fallback_shift_id:
                return s
    emp = db.execute("SELECT shift_id FROM employees WHERE id=?", (emp_id,)).fetchone()
    if emp and emp["shift_id"]:
        for s in all_shifts:
            if s["id"] == emp["shift_id"]:
                return s
    return all_shifts[0] if all_shifts else None


def determine_shift_from_punch(cin, db=None):
    """Determines whether a punch belongs to Shift 1 (08:00-20:00) or Shift 2 (20:00-08:00).
    - Shift 1 (Day Shift): Punch in between 06:00 and 18:00 (e.g. 8:00 AM to 7:30 PM)
    - Shift 2 (Night Shift): Punch in between 18:00 and 06:00 (e.g. 8:00 PM to 8:00 AM)
    """
    db = db or get_db()
    if isinstance(cin, str):
        cin = parse_dt(cin)

    h = cin.hour if cin else 8

    if 6 <= h < 18:
        shift = db.execute(
            "SELECT * FROM shifts WHERE (name LIKE '%Shift 1%' OR start_time LIKE '08:%') AND active=1 ORDER BY id LIMIT 1"
        ).fetchone()
    else:
        shift = db.execute(
            "SELECT * FROM shifts WHERE (name LIKE '%Shift 2%' OR start_time LIKE '20:%') AND active=1 ORDER BY id LIMIT 1"
        ).fetchone()

    if not shift:
        shift = db.execute("SELECT * FROM shifts WHERE active=1 ORDER BY id LIMIT 1").fetchone()
    return shift


_CACHE_LOCK = threading.Lock()
_SHARED_CACHE = {}


def get_shared_cache(key: str, ttl: float = 60.0):
    now_ts = time.time()
    with _CACHE_LOCK:
        item = _SHARED_CACHE.get(key)
        if item and (now_ts - item["ts"] < ttl):
            return item["data"]
    return None


def set_shared_cache(key: str, data):
    with _CACHE_LOCK:
        _SHARED_CACHE[key] = {"data": data, "ts": time.time()}


def clear_shared_cache(key: str = None):
    with _CACHE_LOCK:
        if key:
            _SHARED_CACHE.pop(key, None)
        else:
            _SHARED_CACHE.clear()


def get_all_shifts_cached(db=None):
    cached = get_shared_cache("shifts", ttl=60.0)
    if cached is not None:
        return cached
    db = db or get_db()
    try:
        rows = [dict(r) for r in db.execute("SELECT * FROM shifts WHERE active=1 ORDER BY id").fetchall()]
        set_shared_cache("shifts", rows)
        return rows
    except Exception:
        return [dict(r) for r in db.execute("SELECT * FROM shifts WHERE active=1 ORDER BY id").fetchall()]


def get_upcoming_holiday_cached(db=None):
    cached = get_shared_cache("upcoming_holiday", ttl=300.0)
    if cached is not None:
        return cached
    db = db or get_db()
    try:
        row = db.execute("SELECT * FROM holidays WHERE date>=? ORDER BY date LIMIT 1", (d2s(today()),)).fetchone()
        row_dict = dict(row) if row else None
        set_shared_cache("upcoming_holiday", row_dict)
        return row_dict
    except Exception:
        return None


def resolve_employee_shift(emp_id: int, d=None, check_in=None, db=None, fallback_shift_id=None, emp_obj=None, emp_schedules=None):
    """
    Intelligent Shift Allocation Engine with in-memory caching for sub-millisecond execution.
    """
    db = db or get_db()
    d = d or today()
    d_str = d2s(d) if isinstance(d, (date, datetime)) else str(d)[:10]

    all_shifts = [dict(s) for s in get_all_shifts_cached(db)]
    shift_by_id = {s["id"]: s for s in all_shifts}

    # 1. Weekly shift schedule for target date
    sched = None
    if emp_schedules is not None:
        for s in emp_schedules:
            s_dict = dict(s)
            if s_dict.get("start_date") and s_dict.get("end_date") and s_dict["start_date"] <= d_str <= s_dict["end_date"]:
                sched = s_dict
                break
    else:
        sched = db.execute(
            "SELECT s.* FROM weekly_shift_schedules w JOIN shifts s ON s.id = w.shift_id "
            "WHERE w.emp_id = ? AND w.start_date <= ? AND w.end_date >= ? "
            "ORDER BY w.id DESC LIMIT 1",
            (emp_id, d_str, d_str),
        ).fetchone()

    if sched:
        sched = dict(sched)
        s_name = (sched.get("name") or "").lower()
        if ("shift 1" in s_name or "shift 2" in s_name) and check_in:
            return determine_shift_from_punch(check_in, db) or sched
        return sched

    # 2. Check employee record with category and department (in-memory if emp_obj passed)
    emp = emp_obj
    if emp is None:
        emp = db.execute(
            "SELECT e.*, c.code category_code, c.name category_name "
            "FROM employees e LEFT JOIN employee_categories c ON c.id=e.category_id WHERE e.id=?",
            (emp_id,)
        ).fetchone()

    if emp:
        emp = dict(emp)
        dept = (emp.get("department") or "").strip().lower()
        cat_code = (emp.get("category_code") or "").strip().lower()
        cat_name = (emp.get("category_name") or "").strip().lower()
        is_accounts = ("account" in dept or "finance" in dept or cat_code == "accounts_finance" or "account" in cat_name or "finance" in cat_name)
        is_staff = (cat_code == "staff" or "staff" in dept or "admin" in dept or "hr" in dept or "human" in dept or "it" in dept or emp.get("role") == "hr")

        # ACCOUNTS & FINANCE: Always resolve to Accounts & Finance Shift (09:00-18:00) or General Shift
        if is_accounts:
            for s in all_shifts:
                s_name = (s.get("name") or "").lower()
                if "account" in s_name or "finance" in s_name:
                    return s
            for s in all_shifts:
                if "general" in (s.get("name") or "").lower():
                    return s

        # Assigned shift in employees table or fallback_shift_id
        target_sid = fallback_shift_id or emp.get("shift_id")
        if target_sid and target_sid in shift_by_id:
            s = shift_by_id[target_sid]
            s_name = (s.get("name") or "").lower()
            if ("shift 1" in s_name or "shift 2" in s_name) and check_in:
                return determine_shift_from_punch(check_in, db) or s
            return s

        # STAFF fallback if no shift assigned: General Shift or Staff Shift
        if is_staff:
            for s in all_shifts:
                s_name = (s.get("name") or "").lower()
                if "general" in s_name or "staff" in s_name:
                    return s

    # 3. Fallback to determine from punch time
    if check_in:
        return determine_shift_from_punch(check_in, db)

    # 4. Fallback to first active shift
    return all_shifts[0] if all_shifts else None


def get_all_categories_cached(db=None):
    cached = get_shared_cache("categories", ttl=60.0)
    if cached is not None:
        return cached
    db = db or get_db()
    try:
        rows = [dict(r) for r in db.execute("SELECT * FROM employee_categories ORDER BY id").fetchall()]
        set_shared_cache("categories", rows)
        return rows
    except Exception:
        return [dict(r) for r in db.execute("SELECT * FROM employee_categories ORDER BY id").fetchall()]


def get_all_offices_cached(db=None):
    cached = get_shared_cache("offices", ttl=60.0)
    if cached is not None:
        return cached
    db = db or get_db()
    try:
        rows = [dict(r) for r in db.execute("SELECT * FROM offices WHERE active=1 ORDER BY id").fetchall()]
        set_shared_cache("offices", rows)
        return rows
    except Exception:
        return [dict(r) for r in db.execute("SELECT * FROM offices WHERE active=1 ORDER BY id").fetchall()]


def get_system_settings_cached(db=None):
    cached = get_shared_cache("system_settings", ttl=60.0)
    if cached is not None:
        return cached
    db = db or get_db()
    try:
        rows = db.execute("SELECT key, value FROM system_settings").fetchall()
        settings = {r["key"]: r["value"] for r in rows if r}
        set_shared_cache("system_settings", settings)
        return settings
    except Exception:
        return {}


def get_employee_category(emp_id, db=None):
    """Retrieve employee category details (work hours, overtime, half-day/full-day cutoffs)."""
    db = db or get_db()
    cat_id = None
    if isinstance(emp_id, dict) or hasattr(emp_id, "keys"):
        cat_id = emp_id.get("category_id") if isinstance(emp_id, dict) else (emp_id["category_id"] if "category_id" in emp_id.keys() else None)
        emp_id = emp_id.get("id") if isinstance(emp_id, dict) else (emp_id["id"] if "id" in emp_id.keys() else None)
    cats = [dict(c) for c in get_all_categories_cached(db)]
    if cat_id:
        for c in cats:
            if c["id"] == cat_id:
                return c
    if isinstance(emp_id, int):
        cat = db.execute(
            "SELECT c.* FROM employees e JOIN employee_categories c ON c.id=e.category_id WHERE e.id=?",
            (emp_id,),
        ).fetchone()
        if cat:
            return dict(cat)
    for c in cats:
        if c.get("code") == "EMPLOYEES":
            return c
    return {
        "name": "Employees", "code": "EMPLOYEES", "work_hours": 12.0, "overtime_hours": 0.0,
        "half_day_min_hours": 4.0, "half_day_time": "14:00", "early_full_day_cutoff": "16:00", "full_day_time": "20:00"
    }


# ------------------------------------------------------------------ status rules
def compute_attendance_status(cin: datetime, cout: datetime, shift, category=None):
    """Calculate attendance status and overtime hours based on configured attendance policy,
    employee category working hours, and shift rules.
    Rules:
    - Standard shifts are up to 12.0 hours.
    - If elapsed hours > 12.5 (e.g. employee forgot to punch out or punch-out missing), it is a Missed Punch ('MP') with 0.0 OT.
    - If category does not allow overtime (e.g. Staff/Employees with overtime_hours=0.0), OT is 0.0.
    - 2.0 to 6.0 hours worked: Half Day ('HD').
    - 6.0 to 12.0 hours worked: Full Day Present ('P') or 'OT' if category permits overtime.
    - If employee clocks out after 4:00 PM (16:00) with >= 2.0 hours, counts as Full Day Present ('P').
    - Less than 2.0 hours: Absent ('A') (incomplete work duration).
    """
    if not cin or not cout:
        return "A", 0.0

    seconds = max((cout - cin).total_seconds(), 0)
    hours = seconds / 3600.0

    min_half_hours = float(category["half_day_min_hours"] if category and "half_day_min_hours" in category.keys() else 2.0)
    cat_work_hours = float(category["work_hours"] if category and "work_hours" in category.keys() else (shift["full_hours"] if shift else 8.0))
    cat_ot_allowed = float(category["overtime_hours"] if category and "overtime_hours" in category.keys() else 0.0)
    cutoff_full_str = category["early_full_day_cutoff"] if category and "early_full_day_cutoff" in category.keys() else "16:00"

    # Shifts are 12 hours max. Beyond 12.5 hours without valid checkout, it is a Missed Punch (MP)
    if hours > 12.5:
        return "MP", 0.0

    # Overtime calculation: strictly respects category overtime_hours setting
    if cat_ot_allowed > 0:
        raw_ot = max(hours - cat_work_hours, 0.0)
        ot_hours = round(min(raw_ot, cat_ot_allowed), 2)
    else:
        ot_hours = 0.0

    cutoff_h, cutoff_m = map(int, cutoff_full_str.split(":"))
    out_time = cout.time()
    cutoff_full_time = datetime.min.time().replace(hour=cutoff_h, minute=cutoff_m)

    is_after_4pm = out_time >= cutoff_full_time
    shift_start_h = int(shift["start_time"].split(":")[0]) if shift else 8
    is_day_shift = shift_start_h < 14

    # Rule: 6 to 12 hours (or after 4 PM with >= 2.0h) -> Full Day Present (P or OT)
    if hours >= 6.0 or (is_day_shift and is_after_4pm and hours >= min_half_hours) or hours >= cat_work_hours:
        if ot_hours > 0:
            return "OT", ot_hours
        return "P", 0.0

    # Rule: 2 to 6 hours -> Half Day (HD)
    if hours >= 2.0 or hours >= min_half_hours:
        return "HD", 0.0

    # Rule: Under 2 hours -> Absent (A)
    return "A", 0.0


def compute_status(hours: float, shift):
    """Calculates attendance status based on standard company rules:
    - > 12.5 hours: Missed Punch ('MP')
    - 2.0 to 6.0 hours: Half Day ('HD')
    - 6.0+ hours: Full Day ('P')
    - Overtime: beyond full hours + ot_min_hours
    - < 2.0 hours: Absent ('A')
    """
    if hours > 12.5:
        return "MP", 0.0
    full = shift["full_hours"] if shift else 8.0
    ot_min = shift["ot_min_hours"] if shift else 1.0
    if hours >= max(full, 6.0) + ot_min:
        return "OT", round(hours - max(full, 6.0), 2)
    if hours >= 6.0 or hours >= full:
        return "P", 0.0
    if hours >= 2.0:
        return "HD", 0.0
    return "A", 0.0


def is_late(check_in: datetime, shift) -> bool:
    h, m = map(int, shift["start_time"].split(":"))
    end_h, end_m = map(int, shift["end_time"].split(":"))
    # If night shift crosses midnight (e.g. 20:00 to 08:00) and worker punches in past midnight
    if (end_h < h or (end_h == h and end_m < m)) and check_in.hour < 12:
        start_date = check_in.date() - timedelta(days=1)
    else:
        start_date = check_in.date()
    limit = datetime.combine(start_date, datetime.min.time()).replace(hour=h, minute=m, second=0) + timedelta(minutes=shift["grace_min"])
    return check_in > limit


def finalize_attendance(rec, shift, category=None):
    """Recalculate hours/status/late for an attendance row dict-like. Returns update dict."""
    cin, cout = parse_dt(rec["check_in"]), parse_dt(rec["check_out"])
    upd = {"late": 0, "work_hours": 0.0, "ot_hours": 0.0, "status": None}
    if cin:
        upd["late"] = 1 if is_late(cin, shift) else 0
    if cin and cout:
        hours = max((cout - cin).total_seconds() / 3600.0, 0)
        upd["work_hours"] = round(hours, 2)
        upd["status"], upd["ot_hours"] = compute_attendance_status(cin, cout, shift, category)
        if upd["status"] == "MP":
            upd["work_hours"] = 0.0
            upd["ot_hours"] = 0.0
    return upd


# ------------------------------------------------------------------ leave / OD helpers
def working_days(a: date, b: date) -> float:
    db = get_db()
    hols = {r["date"] for r in db.execute("SELECT date FROM holidays WHERE date BETWEEN ? AND ?", (d2s(a), d2s(b)))}
    off = current_app.config["WEEKLY_OFF"]
    return float(sum(1 for d in daterange(a, b) if d.weekday() not in off and d2s(d) not in hols))


def leave_balance(emp_id: int, year: int):
    """Returns list of dicts: code, name, quota, used, pending, balance"""
    db = get_db()
    out = []
    for t in db.execute("SELECT * FROM leave_types ORDER BY id"):
        rows = db.execute(
            "SELECT status, COALESCE(SUM(days),0) d FROM leaves WHERE emp_id=? AND type_id=? "
            "AND substr(from_date,1,4)=? AND status IN ('Approved','Pending') GROUP BY status",
            (emp_id, t["id"], str(year)),
        ).fetchall()
        used = next((r["d"] for r in rows if r["status"] == "Approved"), 0)
        pend = next((r["d"] for r in rows if r["status"] == "Pending"), 0)
        out.append({"id": t["id"], "code": t["code"], "name": t["name"], "quota": t["annual_quota"],
                    "used": used, "pending": pend, "balance": t["annual_quota"] - used - pend})
    return out


def approved_od_for(emp_id: int, d: date):
    return get_db().execute(
        "SELECT * FROM od_requests WHERE emp_id=? AND od_date=? AND status='Approved'", (emp_id, d2s(d))
    ).fetchone()


def approved_permission_for(emp_id: int, d):
    """Returns approved permission request for employee on target date, or None."""
    d_str = d2s(d) if isinstance(d, (date, datetime)) else str(d)[:10]
    return get_db().execute(
        "SELECT * FROM permission_requests WHERE emp_id=? AND perm_date=? AND status='Approved'",
        (emp_id, d_str)
    ).fetchone()


# ------------------------------------------------------------------ register / reports
def day_code(d, rec, leave, od, holiday, today_, joined, shift=None):
    if rec and rec["check_in"]:
        if rec["check_out"]:
            return rec["status"] or "A"
        cin_dt = parse_dt(rec["check_in"])
        if cin_dt and (now() - cin_dt).total_seconds() >= 12 * 3600:
            return "MP"
        return "IN" if d == today_ else "MP"
    if d < joined:
        return "-"
    if leave:
        return "HL" if leave["half_day"] else "L"
    if od:
        return "OD"
    if holiday:
        return "H"
    if d.weekday() in current_app.config["WEEKLY_OFF"]:
        return "WO"
    if d < today_:
        return "A"
    if d == today_ and shift:
        cur_time = now().strftime("%H:%M")
        start_t = shift["start_time"]
        end_t = shift["end_time"]
        if end_t > start_t and cur_time > end_t:
            return "A"
    return "-"


def build_register(emp_rows, start: date, end: date):
    """Return {emp_id: {"days": {date: code}, "recs": {date: row}, "summary": {...}}}"""
    db = get_db()
    ids = [e["id"] for e in emp_rows]
    if not ids:
        return {}
    qs = ",".join("?" * len(ids))
    s, e = d2s(start), d2s(end)
    att = {}
    for r in db.execute(f"SELECT * FROM attendance WHERE emp_id IN ({qs}) AND date BETWEEN ? AND ?", (*ids, s, e)):
        att[(r["emp_id"], r["date"])] = r
    leaves = db.execute(
        f"SELECT * FROM leaves WHERE emp_id IN ({qs}) AND status='Approved' AND from_date<=? AND to_date>=?", (*ids, e, s)
    ).fetchall()
    ods = {(r["emp_id"], r["od_date"]): r for r in db.execute(
        f"SELECT * FROM od_requests WHERE emp_id IN ({qs}) AND status='Approved' AND od_date BETWEEN ? AND ?", (*ids, s, e))}
    hols = {r["date"]: r["name"] for r in db.execute("SELECT * FROM holidays WHERE date BETWEEN ? AND ?", (s, e))}
    leave_map = {}
    for l in leaves:
        for d in daterange(max(s2d(l["from_date"]), start), min(s2d(l["to_date"]), end)):
            leave_map[(l["emp_id"], d2s(d))] = l

    # Preload weekly shift schedules for all employees in one single query
    schedules = {}
    try:
        for r in db.execute(f"SELECT s.*, w.emp_id, w.start_date, w.end_date FROM weekly_shift_schedules w JOIN shifts s ON s.id = w.shift_id WHERE w.emp_id IN ({qs})", (*ids,)).fetchall():
            schedules.setdefault(r["emp_id"], []).append(r)
    except Exception:
        pass

    t = today()
    result = {}
    for emp in emp_rows:
        days, recs, shifts = {}, {}, {}
        summ = dict.fromkeys(["P", "OT", "HD", "A", "L", "HL", "OD", "H", "WO", "MP", "IN", "late", "hours", "ot_hours"], 0)
        emp_dict = dict(emp) if emp else {}
        joined = s2d(emp_dict["joined_on"]) if emp_dict.get("joined_on") else date(2000, 1, 1)
        emp_scheds = schedules.get(emp["id"], [])
        for d in daterange(start, end):
            k = (emp["id"], d2s(d))
            rec = att.get(k)
            rec_dict = dict(rec) if rec else {}
            shift = resolve_employee_shift(
                emp["id"], d, rec_dict.get("check_in"), db,
                fallback_shift_id=rec_dict.get("shift_id"),
                emp_obj=emp_dict,
                emp_schedules=emp_scheds
            )
            shifts[d] = shift
            code = day_code(d, rec, leave_map.get(k), ods.get(k), hols.get(d2s(d)), t, joined, shift)
            days[d] = code
            if rec:
                recs[d] = rec
                summ["late"] += rec["late"] or 0
                summ["hours"] += rec["work_hours"] or 0
                summ["ot_hours"] += rec["ot_hours"] or 0
            if code in summ:
                summ[code] += 1
        summ["hours"] = round(summ["hours"], 2)
        summ["ot_hours"] = round(summ["ot_hours"], 2)
        # Payable days: worked + paid leave/OD/holiday/weekly-off, half-days count 0.5
        summ["payable"] = (summ["P"] + summ["OT"] + summ["OD"] + summ["L"] + summ["H"] + summ["WO"]
                           + 0.5 * summ["HD"] + 0.5 * summ["HL"])
        result[emp["id"]] = {"days": days, "recs": recs, "summary": summ, "shifts": shifts}
    return result


# ------------------------------------------------------------------ Google Maps link -> coordinates
import re as _re
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


def _dms_to_dd(deg, min, sec, direction):
    dd = float(deg) + float(min) / 60.0 + float(sec) / 3600.0
    if direction.upper() in ("S", "W"):
        dd = -dd
    return dd


def coords_from_text(text):
    text = unquote(text or "").strip()
    if not text:
        return None

    # 1. Degree Minute Second (DMS): 12°44'36.5"N 77°49'02.8"E or similar
    dms_matches = _re.findall(
        r"(\d{1,2})[°\s]+(\d{1,2})['′\s]+([\d.]+)[\"″\s]*([NSEWnsew])", text
    )
    if len(dms_matches) == 2:
        lat = _dms_to_dd(*dms_matches[0])
        lng = _dms_to_dd(*dms_matches[1])
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return round(lat, 6), round(lng, 6)

    # 2. Signed decimal with cardinal direction: 12.743475N, 77.817446E or 12.743475° N, 77.817446° E
    m_card = _re.search(
        r"(-?\d{1,2}\.\d+)\s*[°]?\s*([NSns])\s*[,;\s]+\s*(-?\d{1,3}\.\d+)\s*[°]?\s*([EWew])",
        text,
    )
    if m_card:
        lat = float(m_card.group(1)) * (-1 if m_card.group(2).upper() == "S" else 1)
        lng = float(m_card.group(3)) * (-1 if m_card.group(4).upper() == "W" else 1)
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return round(lat, 6), round(lng, 6)

    # 3. Standard URL and coordinate patterns
    patterns = (
        r"@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)",
        r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)",
        r"[?&](?:q|ll|query|center|destination|daddr|saddr)=(?:loc:)?(-?\d{1,2}\.\d+)[,\s+](-?\d{1,3}\.\d+)",
        r"/(?:place|search)/(-?\d{1,2}\.\d+)[,\s+](-?\d{1,3}\.\d+)",
        r"geo:(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)",
        r"(-?\d{1,2}\.\d{3,})\s*[,;\s]\s*(-?\d{1,3}\.\d{3,})",
    )
    for pat in patterns:
        m = _re.search(pat, text, _re.I)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return round(lat, 6), round(lng, 6)
    return None


def coords_from_maps_link(link):
    """Accepts 'lat,lng' text, a full Google Maps URL, or a maps.app.goo.gl short link
    (which is resolved by following the redirect). Returns (lat, lng) or None."""
    link = (link or "").strip()
    if not link:
        return None
    direct = coords_from_text(link)
    if direct:
        return direct

    # Try following redirects for short links (e.g. maps.app.goo.gl, goo.gl, bit.ly, etc.)
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(link, headers=headers, allow_redirects=True, timeout=8)
        resolved = coords_from_text(resp.url) or coords_from_text(resp.text)
        if resolved:
            return resolved
    except Exception:
        pass

    try:
        req = Request(link, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urlopen(req, timeout=8) as r:
            final = r.geturl()
            body = r.read(400000).decode("utf-8", "ignore")
            return coords_from_text(final) or coords_from_text(body)
    except Exception:
        return None


def check_and_sync_missed_punches(db=None):
    """
    Checks for attendance records where employee punched IN but did NOT punch OUT within 12 hours.
    Automatically:
    1. Marks status = 'MP', work_hours = 0.0, ot_hours = 0.0, check_out = NULL.
    2. Sends an automatic notification to HR (target_role='hr') with employee details and date/time.
    3. Leaves mp_resolved = 0 so it stays in the HR Missed Punch List until HR reviews and resolves it.
    """
    if db is None:
        db = get_db()

    now_dt = now()
    today_s = d2s(today())

    try:
        # Check if mp_resolved column exists
        cols = [c[1] for c in db.execute("PRAGMA table_info(attendance)").fetchall()]
        has_mp_col = "mp_resolved" in cols

        query = """
            SELECT a.*, e.name as emp_name, e.emp_code, e.department, s.name as shift_name
            FROM attendance a
            JOIN employees e ON e.id = a.emp_id
            LEFT JOIN shifts s ON s.id = a.shift_id
            WHERE a.check_in IS NOT NULL 
        """
        if has_mp_col:
            query += " AND (a.mp_resolved IS NULL OR a.mp_resolved = 0)"
        
        rows = db.execute(query).fetchall()
        updated = False

        for r in rows:
            cin_dt = parse_dt(r["check_in"])
            if not cin_dt:
                continue

            elapsed_sec = (now_dt - cin_dt).total_seconds()
            is_past_day = r["date"] < today_s
            is_over_12h = elapsed_sec >= 12 * 3600

            # If unclosed and over 12 hours or past day:
            if (is_past_day or is_over_12h) and (not r["check_out"] or r["status"] == "MP"):
                needs_update = False
                if r["status"] != "MP" or r["work_hours"] != 0.0 or r["ot_hours"] != 0.0 or r["check_out"] is not None or not r["remarks"]:
                    needs_update = True

                if needs_update:
                    mp_remark = "Auto Marked – OUT Punch Not Recorded Within 12 Hours."
                    if has_mp_col:
                        db.execute("""
                            UPDATE attendance 
                            SET status = 'MP', 
                                work_hours = 0.0, 
                                ot_hours = 0.0, 
                                check_out = NULL,
                                out_selfie = NULL,
                                mp_resolved = 0,
                                remarks = ?
                            WHERE id = ?
                        """, (mp_remark, r["id"]))
                    else:
                        db.execute("""
                            UPDATE attendance 
                            SET status = 'MP', 
                                work_hours = 0.0, 
                                ot_hours = 0.0, 
                                check_out = NULL,
                                out_selfie = NULL,
                                remarks = ?
                            WHERE id = ?
                        """, (mp_remark, r["id"]))
                    updated = True

                # Send automatic notification to HR if not already notified
                notif_tag = f"MISSED_PUNCH_ATT_{r['id']}"
                existing_notif = db.execute(
                    "SELECT id FROM notifications WHERE target_role='hr' AND (message LIKE ? OR message LIKE ?)",
                    (f"%{notif_tag}%", f"%{r['emp_code']}%punched IN%{r['date']}%")
                ).fetchone()

                if not existing_notif:
                    cin_time_str = cin_dt.strftime("%I:%M %p")
                    notif_title = f"⚠️ Missed Punch Alert: {r['emp_name']} ({r['emp_code']})"
                    notif_msg = (
                        f"Employee {r['emp_name']} ({r['emp_code']} · {r['department'] or 'Staff'}) "
                        f"punched IN at {cin_time_str} on {r['date']} but did not punch OUT within 12 hours. "
                        f"Auto Marked – OUT Punch Not Recorded Within 12 Hours. Pending HR review. [{notif_tag}]"
                    )
                    create_notification(
                        title=notif_title,
                        message=notif_msg,
                        kind="missed_punch",
                        target_role="hr",
                        link_url="/hr/missed-punches",
                        db=db
                    )
                    updated = True

        if updated:
            try:
                db.commit()
            except Exception:
                pass
    except Exception as e:
        if current_app:
            current_app.logger.warning(f"Error in check_and_sync_missed_punches: {e}")
