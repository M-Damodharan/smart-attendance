"""Business rules: geofence, status calculation, register building, audit log."""
import calendar
import math
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


# ------------------------------------------------------------------ audit
def audit(action, entity=None, entity_id=None, details=None, actor=None):
    db = get_db()
    uid = actor["id"] if actor else session.get("uid")
    name = actor["name"] if actor else session.get("name")
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) if request else None
    db.execute(
        "INSERT INTO audit_log (actor_id, actor_name, action, entity, entity_id, details, ip) VALUES (?,?,?,?,?,?,?)",
        (uid, name, action, entity, str(entity_id) if entity_id is not None else None, details, ip),
    )


# ------------------------------------------------------------------ employee categories & shift schedules
def get_employee_shift_for_date(emp_id: int, d, fallback_shift_id: int = None):
    """Retrieve the effective shift for an employee on a given date, supporting weekly schedules."""
    db = get_db()
    d_str = d2s(d) if isinstance(d, date) else str(d)[:10]
    row = db.execute(
        "SELECT s.* FROM weekly_shift_schedules w JOIN shifts s ON s.id = w.shift_id "
        "WHERE w.emp_id = ? AND w.start_date <= ? AND w.end_date >= ? "
        "ORDER BY w.id DESC LIMIT 1",
        (emp_id, d_str, d_str),
    ).fetchone()
    if row:
        return row
    if fallback_shift_id:
        s = db.execute("SELECT * FROM shifts WHERE id=?", (fallback_shift_id,)).fetchone()
        if s:
            return s
    emp = db.execute("SELECT shift_id FROM employees WHERE id=?", (emp_id,)).fetchone()
    if emp and emp["shift_id"]:
        s = db.execute("SELECT * FROM shifts WHERE id=?", (emp["shift_id"],)).fetchone()
        if s:
            return s
    return db.execute("SELECT * FROM shifts WHERE active=1 ORDER BY id LIMIT 1").fetchone()


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


def get_employee_category(emp_id: int):
    """Retrieve employee category details (work hours, overtime, half-day/full-day cutoffs)."""
    db = get_db()
    cat = db.execute(
        "SELECT c.* FROM employees e JOIN employee_categories c ON c.id=e.category_id WHERE e.id=?",
        (emp_id,),
    ).fetchone()
    if cat:
        return cat
    # Fallback to Employees category
    row = db.execute("SELECT * FROM employee_categories WHERE code='EMPLOYEES'").fetchone()
    if row:
        return row
    return {
        "name": "Employees", "code": "EMPLOYEES", "work_hours": 12.0, "overtime_hours": 0.0,
        "half_day_min_hours": 4.0, "half_day_time": "14:00", "early_full_day_cutoff": "16:00", "full_day_time": "20:00"
    }


# ------------------------------------------------------------------ status rules
def compute_attendance_status(cin: datetime, cout: datetime, shift, category=None):
    """Calculate attendance status and overtime hours based on configured attendance policy,
    employee category working hours, and shift rules.
    Rules:
    - Half Day: 8:00 AM to 2:00 PM (14:00).
    - Full Day: 8:00 AM to 8:00 PM (20:00).
    - If employee clocks out after 4:00 PM (16:00), count day as Full Day Present ('P').
    - Overtime: calculated for hours worked beyond regular category working hours.
    - Category hours: Staff 10h, Employees 12h, Contract Employees 10h regular + 2h OT.
    """
    if not cin or not cout:
        return "A", 0.0

    seconds = max((cout - cin).total_seconds(), 0)
    hours = seconds / 3600.0

    min_half_hours = float(category["half_day_min_hours"] if category and "half_day_min_hours" in category.keys() else (shift["half_hours"] if shift else 4.0))
    cat_work_hours = float(category["work_hours"] if category and "work_hours" in category.keys() else (shift["full_hours"] if shift else 8.0))
    cutoff_full_str = category["early_full_day_cutoff"] if category and "early_full_day_cutoff" in category.keys() else "16:00"
    half_time_str = category["half_day_time"] if category and "half_day_time" in category.keys() else "14:00"

    cutoff_h, cutoff_m = map(int, cutoff_full_str.split(":"))
    half_h, half_m = map(int, half_time_str.split(":"))

    out_time = cout.time()
    cutoff_full_time = datetime.min.time().replace(hour=cutoff_h, minute=cutoff_m)
    half_day_time = datetime.min.time().replace(hour=half_h, minute=half_m)

    # Overtime hours: any time worked beyond required regular category work hours
    ot_hours = round(max(hours - cat_work_hours, 0.0), 2)

    is_after_4pm = out_time >= cutoff_full_time
    is_after_2pm = out_time >= half_day_time

    shift_start_h = int(shift["start_time"].split(":")[0]) if shift else 8
    is_day_shift = shift_start_h < 14

    if is_day_shift:
        # Rule: Clock out after 4:00 PM counts as Full Day Present (requiring at least reasonable work period, e.g. >= min_half_hours)
        if (is_after_4pm and hours >= min_half_hours) or hours >= cat_work_hours:
            if ot_hours > 0:
                return "OT", ot_hours
            return "P", ot_hours
        elif is_after_2pm and hours >= (min_half_hours * 0.75):
            return "HD", 0.0
        elif hours >= min_half_hours:
            return "HD", 0.0
        else:
            return "A", 0.0
    else:
        # Night shifts or non-daytime shifts
        if hours >= cat_work_hours:
            if ot_hours > 0:
                return "OT", ot_hours
            return "P", ot_hours
        elif hours >= min_half_hours:
            return "HD", 0.0
        else:
            return "A", 0.0


def compute_status(hours: float, shift):
    """Legacy helper for backwards compatibility."""
    full = shift["full_hours"] if shift else 8.0
    half = shift["half_hours"] if shift else 4.0
    ot_min = shift["ot_min_hours"] if shift else 1.0
    if hours >= full + ot_min:
        return "OT", round(hours - full, 2)
    if hours >= full:
        return "P", 0.0
    if hours >= half:
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


# ------------------------------------------------------------------ register / reports
def day_code(d, rec, leave, od, holiday, today_, joined, shift=None):
    if rec and rec["check_in"]:
        if rec["check_out"]:
            return rec["status"] or "A"
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

    t = today()
    result = {}
    for emp in emp_rows:
        days, recs = {}, {}
        summ = dict.fromkeys(["P", "OT", "HD", "A", "L", "HL", "OD", "H", "WO", "MP", "IN", "late", "hours", "ot_hours"], 0)
        emp_dict = dict(emp) if emp else {}
        joined = s2d(emp_dict["joined_on"]) if emp_dict.get("joined_on") else date(2000, 1, 1)
        for d in daterange(start, end):
            k = (emp["id"], d2s(d))
            rec = att.get(k)
            rec_dict = dict(rec) if rec else {}
            if rec_dict.get("check_in"):
                shift = determine_shift_from_punch(rec_dict["check_in"], db)
            else:
                shift = get_employee_shift_for_date(emp["id"], d, emp["shift_id"])
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
        result[emp["id"]] = {"days": days, "recs": recs, "summary": summ}
    return result


# ------------------------------------------------------------------ Google Maps link -> coordinates
import re as _re
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

_COORD_PATTERNS = (
    r"@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)",
    r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)",
    r"[?&](?:q|ll|query|center|destination)=(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)",
    r"^\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$",
)


def coords_from_text(text):
    text = unquote(text or "")
    for pat in _COORD_PATTERNS:
        m = _re.search(pat, text)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return lat, lng
    return None


def coords_from_maps_link(link):
    """Accepts 'lat,lng' text, a full Google Maps URL, or a maps.app.goo.gl short link
    (which is resolved by following the redirect). Returns (lat, lng) or None."""
    link = (link or "").strip()
    direct = coords_from_text(link)
    if direct:
        return direct
    host = (urlparse(link).hostname or "").lower()
    if not (host.endswith("google.com") or host.endswith("goo.gl") or host.endswith("g.co")):
        return None  # only follow Google Maps links (prevents the server fetching arbitrary URLs)
    try:
        req = Request(link, headers={"User-Agent": "Mozilla/5.0 (compatible; AttendanceApp)"})
        with urlopen(req, timeout=8) as r:
            final = r.geturl()
            body = r.read(400000).decode("utf-8", "ignore")
    except Exception:
        return None
    return coords_from_text(final) or coords_from_text(body)
