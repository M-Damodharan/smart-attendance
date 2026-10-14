"""Excel export for the monthly attendance report (openpyxl)."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .services import STATUS_LABELS, daterange, d2s

FILL = {
    "P": "C8E6C9", "OT": "D1C4E9", "HD": "FFE0B2", "A": "FFCDD2", "L": "BBDEFB", "HL": "BBDEFB",
    "OD": "B2EBF2", "H": "E0E0E0", "WO": "EEEEEE", "MP": "FFF59D", "IN": "DCEDC8",
}
THIN = Side(style="thin", color="BBBBBB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="0B3D4F")
HEAD_FONT = Font(bold=True, color="FFFFFF")


def _header(ws, row, values, start_col=1):
    for i, v in enumerate(values):
        c = ws.cell(row=row, column=start_col + i, value=v)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def build_workbook(start, end, emps, reg, db):
    wb = Workbook()
    title = start.strftime("%B %Y")

    # ---------------- Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = f"SBG (Sri Balaji Gears) — Monthly Attendance Summary ({title})"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = ("Payable days = P + OT + OD + Leave + Holiday + Weekly Off + 0.5×HD + 0.5×Half-day Leave. "
                "Missed punches (MP) count as 0 until HR corrects them.")
    cols = ["Emp Code", "Name", "Department", "Shift", "Present", "OT days", "Half Day", "Absent", "Leave", "On Duty",
            "Holiday", "Weekly Off", "Missed Punch", "Late", "Work Hrs", "OT Hrs", "Payable Days"]
    _header(ws, 4, cols)
    r = 5
    for e in emps:
        s = reg[e["id"]]["summary"]
        row = [e["emp_code"], e["name"], e["department"], e["shift_name"], s["P"], s["OT"], s["HD"], s["A"],
               s["L"] + 0.5 * s["HL"], s["OD"], s["H"], s["WO"], s["MP"], s["late"], s["hours"], s["ot_hours"], s["payable"]]
        for i, v in enumerate(row, 1):
            c = ws.cell(row=r, column=i, value=v)
            c.border = BORDER
            if i > 4:
                c.alignment = Alignment(horizontal="center")
        r += 1
    for i, w in enumerate([11, 24, 18, 10] + [10] * 13, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[4].height = 32
    ws.freeze_panes = "C5"

    # ---------------- Day-wise grid
    wg = wb.create_sheet("Day-wise")
    days = list(daterange(start, end))
    wg["A1"] = f"SBG (Sri Balaji Gears) — Day-wise Register ({title})"
    wg["A1"].font = Font(bold=True, size=14)
    _header(wg, 3, ["Emp Code", "Name"] + [d.day for d in days])
    _header(wg, 4, ["", ""] + [d.strftime("%a") for d in days])
    r = 5
    for e in emps:
        wg.cell(row=r, column=1, value=e["emp_code"]).border = BORDER
        wg.cell(row=r, column=2, value=e["name"]).border = BORDER
        for j, d in enumerate(days):
            code = reg[e["id"]]["days"][d]
            c = wg.cell(row=r, column=3 + j, value="" if code == "-" else code)
            c.alignment = Alignment(horizontal="center")
            c.border = BORDER
            if code in FILL:
                c.fill = PatternFill("solid", fgColor=FILL[code])
        r += 1
    wg.column_dimensions["A"].width = 11
    wg.column_dimensions["B"].width = 24
    for j in range(len(days)):
        wg.column_dimensions[get_column_letter(3 + j)].width = 4.5
    wg.freeze_panes = "C5"
    r += 1
    wg.cell(row=r, column=1, value="Legend").font = Font(bold=True)
    for code, label in STATUS_LABELS.items():
        if code in ("-", "IN"):
            continue
        r += 1
        c = wg.cell(row=r, column=1, value=code)
        c.fill = PatternFill("solid", fgColor=FILL.get(code, "FFFFFF"))
        c.alignment = Alignment(horizontal="center")
        wg.cell(row=r, column=2, value=label)

    # ---------------- Punch details
    wd = wb.create_sheet("Punch Details")
    _header(wd, 1, ["Emp Code", "Name", "Date", "Day", "Shift", "Check-in", "Check-out", "Work Hrs", "OT Hrs", "Status",
                    "Late", "In Dist (m)", "Out Dist (m)", "Source", "Remarks"])
    r = 2
    for e in emps:
        for d in days:
            rec = reg[e["id"]]["recs"].get(d)
            if not rec:
                continue
            vals = [e["emp_code"], e["name"], d2s(d), d.strftime("%a"), e["shift_name"],
                    (rec["check_in"] or "")[11:16], (rec["check_out"] or "")[11:16], rec["work_hours"], rec["ot_hours"],
                    reg[e["id"]]["days"][d], "Yes" if rec["late"] else "", rec["in_dist"], rec["out_dist"],
                    rec["source"], rec["remarks"] or ""]
            for i, v in enumerate(vals, 1):
                wd.cell(row=r, column=i, value=v).border = BORDER
            r += 1
    for i, w in enumerate([11, 24, 12, 6, 10, 10, 10, 9, 8, 8, 6, 11, 11, 11, 30], 1):
        wd.column_dimensions[get_column_letter(i)].width = w
    wd.freeze_panes = "A2"

    # ---------------- Leave & OD
    wl = wb.create_sheet("Leave & OD")
    _header(wl, 1, ["Type", "Emp Code", "Name", "From", "To", "Days", "Category / Location", "Reason / Purpose", "Status", "HR Remarks"])
    r = 2
    s, e_ = d2s(start), d2s(end)
    for x in db.execute("SELECT l.*, e.emp_code, e.name, t.code tcode FROM leaves l JOIN employees e ON e.id=l.emp_id "
                        "JOIN leave_types t ON t.id=l.type_id WHERE l.from_date<=? AND l.to_date>=? AND l.status<>'Cancelled' "
                        "ORDER BY l.from_date", (e_, s)):
        for i, v in enumerate(["Leave", x["emp_code"], x["name"], x["from_date"], x["to_date"], x["days"], x["tcode"],
                               x["reason"], x["status"], x["hr_remarks"]], 1):
            wl.cell(row=r, column=i, value=v).border = BORDER
        r += 1
    for x in db.execute("SELECT o.*, e.emp_code, e.name FROM od_requests o JOIN employees e ON e.id=o.emp_id "
                        "WHERE o.od_date BETWEEN ? AND ? AND o.status<>'Cancelled' ORDER BY o.od_date", (s, e_)):
        for i, v in enumerate(["OD", x["emp_code"], x["name"], x["od_date"], x["od_date"], 1, x["location"],
                               x["purpose"], x["status"], x["hr_remarks"]], 1):
            wl.cell(row=r, column=i, value=v).border = BORDER
        r += 1
    for i, w in enumerate([8, 11, 24, 12, 12, 7, 24, 34, 11, 26], 1):
        wl.column_dimensions[get_column_letter(i)].width = w
    wl.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
