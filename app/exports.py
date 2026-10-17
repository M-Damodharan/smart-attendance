"""Excel export for the monthly attendance report and individual employee attendance documents (openpyxl).
Supports:
1. Category-wise attendance export (Staff, Employees, Contract Employees)
2. Individual Employee Attendance Document ({emp_code}_attendance_{month}.xlsx)
   - Day 1 to Day 31 columns / rows
   - Total days come (e.g. 30 days present)
   - Contract employee day attendance and OT details (e.g. "10 hrs present + 2 hrs OT")
"""
import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .services import STATUS_LABELS, daterange, d2s, now, parse_dt, format_duration, get_employee_category

FILL = {
    "P": "C8E6C9", "OT": "D1C4E9", "HD": "FFE0B2", "A": "FFCDD2", "L": "BBDEFB", "HL": "BBDEFB",
    "OD": "B2EBF2", "H": "E0E0E0", "WO": "EEEEEE", "MP": "FFF59D", "IN": "DCEDC8",
}
THIN = Side(style="thin", color="CCCCCC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="0B3D4F")
HEAD_FONT = Font(bold=True, color="FFFFFF")
SUBHEAD_FILL = PatternFill("solid", fgColor="E0F2F1")
HIGHLIGHT_FILL = PatternFill("solid", fgColor="FFF9C4")


def _header(ws, row, values, start_col=1, fill=HEAD_FILL, font=HEAD_FONT):
    for i, v in enumerate(values):
        c = ws.cell(row=row, column=start_col + i, value=v)
        c.fill, c.font, c.border = fill, font, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def build_employee_attendance_doc(emp, start, end, db):
    """Builds a comprehensive individual attendance document named with the employee's ID.
    Features:
    - Day 1 to 31 day-by-day attendance log with status ('P', 'HD', 'A', etc.)
    - Total days come / attended (e.g. 30 days come)
    - Contract employee work hours + overtime breakdown: e.g. "10 hrs present + 2 hrs OT"
    - Formatted summary KPI totals (regular hours, overtime hours, payable days)
    """
    from .services import build_register
    wb = Workbook()
    ws = wb.active
    emp_d = dict(emp) if emp else {}
    ws.title = f"{emp_d.get('emp_code', 'EMP')} Attendance"

    cat = get_employee_category(emp_d.get("id"))
    cat_d = dict(cat) if cat else {}
    cat_name = cat_d.get("name", "Employees")
    cat_code = cat_d.get("code", "EMPLOYEES")
    cat_work_h = float(cat_d.get("work_hours", 12.0) or 12.0)
    cat_ot_h = float(cat_d.get("overtime_hours", 0.0) or 0.0)
    is_contract = cat_code == "CONTRACT" or "contract" in cat_name.lower()

    reg = build_register([emp_d], start, end)[emp_d["id"]]
    days = list(daterange(start, end))
    summary = reg["summary"]

    # Calculate total days employee came / attended
    total_days_come = sum(1 for d in days if reg["days"][d] in ("P", "OT", "HD", "IN", "MP"))

    # 1. Document Title
    month_title = start.strftime("%B %Y")
    ws.merge_cells("A1:J1")
    title_cell = ws["A1"]
    title_cell.value = "SRI BALAJI GEARS (SBG) — MONTHLY ATTENDANCE DOCUMENT"
    title_cell.font = Font(bold=True, size=14, color="FFFFFF")
    title_cell.fill = HEAD_FILL
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:J2")
    sub_cell = ws["A2"]
    sub_cell.value = f"Attendance Register Period: {month_title} (Day 1 to Day {len(days)}) | Generated on: {now().strftime('%d %b %Y, %I:%M %p')}"
    sub_cell.font = Font(italic=True, size=10, color="FFFFFF")
    sub_cell.fill = PatternFill("solid", fgColor="1B5E20")
    sub_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20

    # 2. Employee Profile & Category Policy Info Box
    info_rows = [
        ("Employee ID (Code):", emp_d.get("emp_code", ""), "Employee Name:", emp_d.get("name", ""), "Category:", f"{cat_name} ({int(cat_work_h)}h/day)"),
        ("Department:", emp_d.get("department") or "General", "Designation:", emp_d.get("designation") or "Staff", "Assigned Shift:", emp_d.get("shift_name") or "Shift 1"),
    ]
    for r_idx, (l1, v1, l2, v2, l3, v3) in enumerate(info_rows, start=4):
        ws.cell(row=r_idx, column=1, value=l1).font = Font(bold=True)
        ws.cell(row=r_idx, column=2, value=v1)
        ws.cell(row=r_idx, column=4, value=l2).font = Font(bold=True)
        ws.cell(row=r_idx, column=5, value=v2)
        ws.cell(row=r_idx, column=7, value=l3).font = Font(bold=True)
        ws.cell(row=r_idx, column=8, value=v3)
        ws.row_dimensions[r_idx].height = 20

    # Category Policy Banner
    ws.merge_cells("A6:J6")
    pol_cell = ws["A6"]
    if is_contract:
        pol_cell.value = "📋 Category Attendance Policy: Contract Employee — Standard 10 Regular Working Hours + 2 Hours Overtime (OT) per day."
    elif "staff" in cat_name.lower():
        pol_cell.value = "📋 Category Attendance Policy: Staff — Standard 10 Working Hours per day (Hours beyond 10 count as Overtime)."
    else:
        pol_cell.value = "📋 Category Attendance Policy: Employees — Standard 12 Working Hours per day."
    pol_cell.font = Font(bold=True, size=10, color="0B3D4F")
    pol_cell.fill = SUBHEAD_FILL
    pol_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[6].height = 22

    # 3. Table Column Headers
    cols = [
        "Day #", "Date", "Day", "Status", "Check In", "Check Out",
        "Regular Hrs", "OT Hrs", "Total Hrs", "Daily Attendance & Overtime Details"
    ]
    _header(ws, 8, cols)
    ws.row_dimensions[8].height = 26

    # 4. Day-by-Day Rows (Day 1 to 31)
    r = 9
    total_reg_hours = 0.0
    for idx, d in enumerate(days, 1):
        code = reg["days"][d]
        raw_rec = reg["recs"].get(d)
        rec = dict(raw_rec) if raw_rec else {}
        cin = rec.get("check_in")
        cout = rec.get("check_out")
        cin_s = parse_dt(cin).strftime("%I:%M %p") if cin else "—"
        cout_s = parse_dt(cout).strftime("%I:%M %p") if cout else "—"

        wh = float(rec.get("work_hours", 0.0) or 0.0)
        ot = float(rec.get("ot_hours", 0.0) or 0.0)
        reg_h = max(wh - ot, 0.0)
        total_reg_hours += reg_h

        # Build human-readable detail note
        if code in ("P", "OT"):
            if is_contract:
                if ot > 0:
                    details = f"{reg_h:.0f} hrs present + {ot:.1f} hrs OT present"
                else:
                    details = f"{wh:.0f} hrs present (10 hrs regular)"
            else:
                if ot > 0:
                    details = f"{wh:.1f} hrs present (incl. {ot:.1f} hrs OT)"
                else:
                    details = f"{wh:.1f} hrs present"
        elif code == "HD":
            details = f"Half Day present ({wh:.1f} hrs worked)"
        elif code == "IN":
            details = f"Working in progress (In at {cin_s})"
        elif code == "A":
            details = "Absent (No punch recorded)"
        elif code == "WO":
            details = "Weekly Off"
        elif code == "H":
            details = "Company Holiday"
        elif code in ("L", "HL"):
            details = "Approved Leave"
        elif code == "OD":
            details = "On Duty (OD)"
        elif code == "MP":
            details = "Missed Punch (Check-out missing)"
        else:
            details = "Not Checked In"

        row_vals = [
            f"Day {d.day}",
            d.strftime("%Y-%m-%d"),
            d.strftime("%a"),
            code,
            cin_s,
            cout_s,
            round(reg_h, 2) if wh > 0 else 0.0,
            round(ot, 2) if ot > 0 else 0.0,
            round(wh, 2) if wh > 0 else 0.0,
            details,
        ]

        for i, val in enumerate(row_vals, 1):
            cell = ws.cell(row=r, column=i, value=val)
            cell.border = BORDER
            if i in (1, 2, 3, 4, 5, 6):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif i in (7, 8, 9):
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

            # Color status cell
            if i == 4 and code in FILL:
                cell.fill = PatternFill("solid", fgColor=FILL[code])
                cell.font = Font(bold=True)

        ws.row_dimensions[r].height = 20
        r += 1

    # 5. Monthly Summary Box (KPI block)
    r += 1
    ws.merge_cells(f"A{r}:J{r}")
    sum_title = ws[f"A{r}"]
    sum_title.value = f"MONTHLY ATTENDANCE SUMMARY & PAYABLE TOTALS — {month_title}"
    sum_title.font = Font(bold=True, size=11, color="FFFFFF")
    sum_title.fill = HEAD_FILL
    sum_title.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[r].height = 24
    r += 1

    kpis = [
        ("Total Days in Month:", len(days), "Total Days Come / Attended:", f"{total_days_come} days"),
        ("Full Days Present (P):", summary["P"], "Overtime Days (OT):", summary["OT"]),
        ("Half Days (HD):", summary["HD"], "Absent Days (A):", summary["A"]),
        ("Approved Leaves / OD:", summary["L"] + summary["HL"] + summary["OD"], "Weekly Off / Holidays:", summary["WO"] + summary["H"]),
        ("Total Regular Work Hours:", f"{total_reg_hours:.1f} hrs", "Total Overtime (OT) Hours:", f"{summary['ot_hours']:.1f} hrs"),
        ("Total Working Hours:", f"{summary['hours']:.1f} hrs", "TOTAL PAYABLE DAYS:", f"{summary['payable']} days"),
    ]

    for label1, val1, label2, val2 in kpis:
        c1 = ws.cell(row=r, column=1, value=label1)
        c2 = ws.cell(row=r, column=3, value=val1)
        c3 = ws.cell(row=r, column=5, value=label2)
        c4 = ws.cell(row=r, column=7, value=val2)

        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
        ws.merge_cells(start_row=r, start_column=5, end_row=r, end_column=6)
        ws.merge_cells(start_row=r, start_column=7, end_row=r, end_column=10)

        c1.font = Font(bold=True)
        c3.font = Font(bold=True)
        if "PAYABLE" in label2 or "Days Come" in label2:
            c3.fill = HIGHLIGHT_FILL
            c4.fill = HIGHLIGHT_FILL
            c4.font = Font(bold=True, size=11, color="1B5E20")

        c1.border = BORDER
        c2.border = BORDER
        c3.border = BORDER
        c4.border = BORDER
        ws.row_dimensions[r].height = 22
        r += 1

    # Column widths
    col_widths = [10, 13, 10, 10, 12, 12, 14, 12, 12, 38]
    for idx, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    ws.freeze_panes = "A9"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_workbook(start, end, emps, reg, db, category_info=None):
    """Builds the comprehensive monthly attendance workbook (openpyxl).
    Includes:
    - Summary Sheet with Category and Total Days Come
    - Day-wise Register (Day 1 to 31) with Total Days Come
    - Contract Attendance & OT Details Sheet (explicit breakdown of 10h regular + 2h OT)
    - Detailed Punch Logs
    - Leave & OD Register
    """
    wb = Workbook()
    title = start.strftime("%B %Y")
    days = list(daterange(start, end))

    cat_info_d = dict(category_info) if category_info else {}
    cat_name = cat_info_d.get("name", "All Categories")
    cat_code = cat_info_d.get("code", "ALL")

    # ---------------- 1. Summary Sheet
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = f"SBG (Sri Balaji Gears) — Monthly Attendance Summary ({title})"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (
        f"Category: {cat_name} | Payable days = P + OT + OD + Leave + Holiday + Weekly Off + 0.5×HD + 0.5×Half-day Leave. "
        "Total Days Come = days attended (P, OT, HD, IN, MP)."
    )
    cols = [
        "Emp Code", "Name", "Category", "Department", "Shift", "Total Days Come",
        "Present (P)", "OT days", "Half Day", "Absent", "Leave", "On Duty",
        "Holiday", "Weekly Off", "Missed Punch", "Late", "Work Hrs", "OT Hrs", "Payable Days"
    ]
    _header(ws, 4, cols)
    r = 5
    for e in emps:
        e_d = dict(e)
        s = reg[e_d["id"]]["summary"]
        days_come = sum(1 for d in days if reg[e_d["id"]]["days"][d] in ("P", "OT", "HD", "IN", "MP"))
        cat_disp = e_d.get("category_name") or "Employees"
        row = [
            e_d["emp_code"], e_d["name"], cat_disp, e_d.get("department") or "General", e_d.get("shift_name") or "Shift 1",
            days_come, s["P"], s["OT"], s["HD"], s["A"], s["L"] + 0.5 * s["HL"], s["OD"],
            s["H"], s["WO"], s["MP"], s["late"], s["hours"], s["ot_hours"], s["payable"]
        ]
        for i, v in enumerate(row, 1):
            c = ws.cell(row=r, column=i, value=v)
            c.border = BORDER
            if i in (1, 5, 6):
                c.alignment = Alignment(horizontal="center")
            elif i > 6:
                c.alignment = Alignment(horizontal="right")
        r += 1

    widths = [12, 24, 18, 16, 12, 16] + [10] * 13
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[4].height = 32
    ws.freeze_panes = "C5"

    # ---------------- 2. Day-wise grid (Day 1 to 31)
    wg = wb.create_sheet("Day-wise (Day 1 to 31)")
    wg["A1"] = f"SBG (Sri Balaji Gears) — Day-wise Register Day 1 to Day {len(days)} ({title})"
    wg["A1"].font = Font(bold=True, size=14)
    _header(wg, 3, ["Emp Code", "Name", "Category", "Total Days Come"] + [f"Day {d.day}" for d in days])
    _header(wg, 4, ["", "", "", ""] + [d.strftime("%a") for d in days])
    r = 5
    for e in emps:
        e_d = dict(e)
        days_come = sum(1 for d in days if reg[e_d["id"]]["days"][d] in ("P", "OT", "HD", "IN", "MP"))
        wg.cell(row=r, column=1, value=e_d["emp_code"]).border = BORDER
        wg.cell(row=r, column=2, value=e_d["name"]).border = BORDER
        wg.cell(row=r, column=3, value=e_d.get("category_name") or "Employees").border = BORDER
        dc_cell = wg.cell(row=r, column=4, value=f"{days_come} days")
        dc_cell.border = BORDER
        dc_cell.alignment = Alignment(horizontal="center")
        dc_cell.font = Font(bold=True)

        for j, d in enumerate(days):
            code = reg[e_d["id"]]["days"][d]
            c = wg.cell(row=r, column=5 + j, value="" if code == "-" else code)
            c.alignment = Alignment(horizontal="center")
            c.border = BORDER
            if code in FILL:
                c.fill = PatternFill("solid", fgColor=FILL[code])
        r += 1
    wg.column_dimensions["A"].width = 11
    wg.column_dimensions["B"].width = 24
    wg.column_dimensions["C"].width = 16
    wg.column_dimensions["D"].width = 16
    for j in range(len(days)):
        wg.column_dimensions[get_column_letter(5 + j)].width = 7.5
    wg.freeze_panes = "E5"

    # Legend
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

    # ---------------- 3. Contract Attendance & OT Details Sheet
    wc = wb.create_sheet("Contract Attendance & OT")
    wc["A1"] = f"SBG (Sri Balaji Gears) — Daily Attendance & Overtime (OT) Report ({title})"
    wc["A1"].font = Font(bold=True, size=14)
    wc["A2"] = "Shows regular working hours + overtime (OT) breakdown. For Contract: 10h regular + 2h OT."

    _header(wc, 4, ["Emp Code", "Name", "Category", "Total Days Come", "Total Work Hrs", "Total OT Hrs"] + [f"Day {d.day}" for d in days])
    _header(wc, 5, ["", "", "", "", "", ""] + [d.strftime("%a") for d in days])

    r = 6
    for e in emps:
        e_d = dict(e)
        s = reg[e_d["id"]]["summary"]
        days_come = sum(1 for d in days if reg[e_d["id"]]["days"][d] in ("P", "OT", "HD", "IN", "MP"))
        cat_disp = e_d.get("category_name") or "Employees"
        is_cnt = "contract" in cat_disp.lower() or (e_d.get("category_code") == "CONTRACT")

        wc.cell(row=r, column=1, value=e_d["emp_code"]).border = BORDER
        wc.cell(row=r, column=2, value=e_d["name"]).border = BORDER
        wc.cell(row=r, column=3, value=cat_disp).border = BORDER
        c_come = wc.cell(row=r, column=4, value=f"{days_come} days")
        c_come.border = BORDER
        c_come.alignment = Alignment(horizontal="center")
        c_come.font = Font(bold=True)
        wc.cell(row=r, column=5, value=s["hours"]).border = BORDER
        wc.cell(row=r, column=6, value=s["ot_hours"]).border = BORDER

        for j, d in enumerate(days):
            code = reg[e_d["id"]]["days"][d]
            raw_rec = reg[e_d["id"]]["recs"].get(d)
            rec = dict(raw_rec) if raw_rec else {}
            wh = float(rec.get("work_hours", 0.0) or 0.0)
            ot = float(rec.get("ot_hours", 0.0) or 0.0)
            reg_h = max(wh - ot, 0.0)

            c = wc.cell(row=r, column=7 + j)
            c.border = BORDER
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            if code in ("P", "OT"):
                if is_cnt:
                    c.value = f"{reg_h:.0f}h+{ot:.0f}h OT" if ot > 0 else f"{wh:.0f}h"
                else:
                    c.value = f"{wh:.0f}h+{ot:.0f}h OT" if ot > 0 else f"{wh:.0f}h"
                c.fill = PatternFill("solid", fgColor=FILL["P"])
            elif code == "HD":
                c.value = f"HD ({wh:.0f}h)"
                c.fill = PatternFill("solid", fgColor=FILL["HD"])
            elif code in FILL:
                c.value = code
                c.fill = PatternFill("solid", fgColor=FILL[code])
            else:
                c.value = ""

        r += 1

    wc.column_dimensions["A"].width = 11
    wc.column_dimensions["B"].width = 24
    wc.column_dimensions["C"].width = 18
    wc.column_dimensions["D"].width = 16
    wc.column_dimensions["E"].width = 14
    wc.column_dimensions["F"].width = 14
    for j in range(len(days)):
        wc.column_dimensions[get_column_letter(7 + j)].width = 11
    wc.freeze_panes = "G6"

    # ---------------- 4. Punch details
    wd = wb.create_sheet("Punch Details")
    _header(wd, 1, ["Emp Code", "Name", "Category", "Date", "Day", "Shift", "Check-in", "Check-out", "Regular Hrs", "OT Hrs", "Total Hrs", "Status",
                    "Late", "In Dist (m)", "Out Dist (m)", "Source", "Remarks"])
    r = 2
    for e in emps:
        e_d = dict(e)
        for d in days:
            raw_rec = reg[e_d["id"]]["recs"].get(d)
            if not raw_rec:
                continue
            rec = dict(raw_rec)
            wh = float(rec.get("work_hours", 0.0) or 0.0)
            ot = float(rec.get("ot_hours", 0.0) or 0.0)
            reg_h = max(wh - ot, 0.0)

            vals = [
                e_d["emp_code"], e_d["name"], e_d.get("category_name") or "Employees", d2s(d), d.strftime("%a"), e_d.get("shift_name") or "Shift 1",
                (rec.get("check_in") or "")[11:16], (rec.get("check_out") or "")[11:16],
                round(reg_h, 2), round(ot, 2), round(wh, 2),
                reg[e_d["id"]]["days"][d], "Yes" if rec.get("late") else "",
                rec.get("in_dist"), rec.get("out_dist"),
                rec.get("source"), rec.get("remarks") or ""
            ]
            for i, v in enumerate(vals, 1):
                wd.cell(row=r, column=i, value=v).border = BORDER
            r += 1
    for i, w in enumerate([11, 24, 16, 12, 6, 10, 10, 10, 11, 9, 10, 8, 6, 11, 11, 11, 30], 1):
        wd.column_dimensions[get_column_letter(i)].width = w
    wd.freeze_panes = "A2"

    # ---------------- 5. Leave & OD
    wl = wb.create_sheet("Leave & OD")
    _header(wl, 1, ["Type", "Emp Code", "Name", "From", "To", "Days", "Category / Location", "Reason / Purpose", "Status", "HR Remarks"])
    r = 2
    s, e_ = d2s(start), d2s(end)
    for x in db.execute("SELECT l.*, e.emp_code, e.name, t.code tcode FROM leaves l JOIN employees e ON e.id=l.emp_id "
                        "JOIN leave_types t ON t.id=l.type_id WHERE l.from_date<=? AND l.to_date>=? AND l.status<>'Cancelled' "
                        "ORDER BY l.from_date", (e_, s)):
        x_d = dict(x)
        for i, v in enumerate(["Leave", x_d["emp_code"], x_d["name"], x_d["from_date"], x_d["to_date"], x_d["days"], x_d["tcode"],
                               x_d["reason"], x_d["status"], x_d["hr_remarks"]], 1):
            wl.cell(row=r, column=i, value=v).border = BORDER
        r += 1
    for x in db.execute("SELECT o.*, e.emp_code, e.name FROM od_requests o JOIN employees e ON e.id=o.emp_id "
                        "WHERE o.od_date BETWEEN ? AND ? AND o.status<>'Cancelled' ORDER BY o.od_date", (s, e_)):
        x_d = dict(x)
        for i, v in enumerate(["OD", x_d["emp_code"], x_d["name"], x_d["od_date"], x_d["od_date"], 1, x_d["location"],
                               x_d["purpose"], x_d["status"], x_d["hr_remarks"]], 1):
            wl.cell(row=r, column=i, value=v).border = BORDER
        r += 1
    for i, w in enumerate([8, 11, 24, 12, 12, 7, 24, 34, 11, 26], 1):
        wl.column_dimensions[get_column_letter(i)].width = w
    wl.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
