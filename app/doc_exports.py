"""Universal Enterprise PDF, Excel, CSV, and Word (DOCX) Document Export Engine.

Supports:
1. Attendance Reports (Daily Register, Date Range, Status & Dept Filters)
2. Employee Details (Master Employee Directory, Categories, Shifts, Active Status)
3. Salary & Payroll Reports (Monthly Payroll Register, Payslips, Allowances & Deductions)
4. Leave Reports (Leave Requests History, Approvals, Quotas & Balances)
5. Monthly Summaries (Muster Roll Summary, Total Work/OT Hours, Payable Days)

Features:
- Professional Corporate Layout with Company Logo, Company Name & Metadata
- A4 Portrait and Landscape Orientations
- Precise Column Width Allocation & Text Alignment (Left text, Center codes/dates, Right numbers/currency)
- Strict Word-Wrapping via ReportLab Paragraphs (Zero text clipping, overlapping, or uneven lines)
- Running Two-Pass NumberedCanvas ("Page X of Y" + Confidential Running Header + Signatures)
- Excel (XLSX) with Corporate Styling, Header Fills, Formatted Numbers & Auto-Fit Columns
- Clean UTF-8 CSV with Excel BOM
- Word (DOCX) with Styled Corporate Tables, Header Logo & Metadata Box
"""
import csv
import io
import os
from datetime import datetime, date

from openpyxl import Workbook
from openpyxl.styles import Alignment as XlAlignment, Border as XlBorder, Font as XlFont, PatternFill as XlFill, Side as XlSide
from openpyxl.utils import get_column_letter

import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Image as RlImage, Spacer, KeepTogether
from reportlab.pdfgen import canvas

from .services import daterange, d2s, s2d, now, today, parse_dt, format_duration


# =============================================================================
# ReportLab Two-Pass Numbered Canvas for "Page X of Y" & Running Corporate Headers
# =============================================================================
class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and draw exact 'Page X of Y' page numbers
    and running corporate header/footer borders on every generated PDF page."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        w, h = self._pagesize

        # Running Top Header on Page 2 and later
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(colors.HexColor("#0F2C4A"))
            self.drawString(36, h - 24, "SRI BALAJI GEARS (SBG) · SMART ATTENDANCE")
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawRightString(w - 36, h - 24, "Confidential Internal Document")

            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(36, h - 28, w - 36, h - 28)

        # Running Bottom Footer Line on all pages
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(36, 32, w - 36, 32)

        # Footer Meta & Pagination
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(36, 20, "Sri Balaji Gears (SBG) · Hosur Plant · Generated via Smart Attendance System")
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(w - 36, 20, page_str)

        self.restoreState()


# =============================================================================
# PDF Builder Engine (ReportLab Platypus)
# =============================================================================
def build_pdf_document(title, metadata, headers, rows, col_widths=None,
                       orientation="portrait", summary_kpis=None,
                       alignments=None, logo_path=None):
    """Generates an enterprise-grade PDF with clean corporate branding, zero text overlap,
    and automatic column width management."""
    buf = io.BytesIO()
    page_size = landscape(A4) if orientation.lower() == "landscape" else A4
    page_w, page_h = page_size
    margin = 36  # 0.5 inch margins
    printable_w = page_w - (margin * 2)

    doc = SimpleDocTemplate(
        buf,
        pagesize=page_size,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin + 6,
    )

    styles = getSampleStyleSheet()

    # Corporate Typography Styles
    title_style = ParagraphStyle(
        name="DocTitle",
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#0F2C4A"),
    )
    sub_title_style = ParagraphStyle(
        name="DocSubtitle",
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#64748B"),
    )
    section_h_style = ParagraphStyle(
        name="SectionHeader",
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=13,
        textColor=colors.HexColor("#0F2C4A"),
        spaceBefore=8,
        spaceAfter=4,
    )

    cell_style_left = ParagraphStyle(
        name="CellLeft",
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#0F172A"),
        alignment=TA_LEFT,
    )
    cell_style_center = ParagraphStyle(
        name="CellCenter",
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#0F172A"),
        alignment=TA_CENTER,
    )
    cell_style_right = ParagraphStyle(
        name="CellRight",
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#0F172A"),
        alignment=TA_RIGHT,
    )

    head_style_left = ParagraphStyle(
        name="HeadLeft",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=TA_LEFT,
    )
    head_style_center = ParagraphStyle(
        name="HeadCenter",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=TA_CENTER,
    )
    head_style_right = ParagraphStyle(
        name="HeadRight",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=TA_RIGHT,
    )

    story = []

    # 1. Header Banner Table (Logo + Title + Meta Box)
    logo_img = None
    if logo_path and os.path.exists(logo_path):
        try:
            logo_img = RlImage(logo_path, width=44, height=44)
        except Exception:
            logo_img = None

    header_text = [
        Paragraph("SRI BALAJI GEARS (SBG)", title_style),
        Paragraph("Industrial Estate, Hosur, Tamil Nadu · Smart Attendance &amp; Workforce Intelligence", sub_title_style),
        Paragraph(f"<b>Report:</b> {title}", ParagraphStyle(name="RepTitle", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=colors.HexColor("#0284C7"))),
    ]

    date_str = now().strftime("%d %b %Y, %I:%M %p")
    meta_box_text = [
        Paragraph(f"<b>Generated:</b> {date_str}", sub_title_style),
        Paragraph(f"<b>Format:</b> A4 {orientation.title()}", sub_title_style),
        Paragraph("<b>Status:</b> Official Internal Document", sub_title_style),
    ]

    header_table_data = []
    if logo_img:
        header_table_data.append([logo_img, header_text, meta_box_text])
        header_table = Table(header_table_data, colWidths=[52, printable_w - 200, 148])
    else:
        header_table_data.append([header_text, meta_box_text])
        header_table = Table(header_table_data, colWidths=[printable_w - 150, 150])

    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 6))

    # Divider Line
    div_table = Table([[""]], colWidths=[printable_w], rowHeights=[2])
    div_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0F2C4A")),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(div_table)
    story.append(Spacer(1, 8))

    # 2. Metadata / Filter Badges (if provided)
    if metadata:
        meta_cells = []
        for item in metadata:
            k = item.get("label", "")
            v = item.get("value", "")
            meta_cells.append(Paragraph(f"<b>{k}:</b> {v}", sub_title_style))

        # Split into rows of up to 4 items
        chunk_size = 4 if orientation == "landscape" else 3
        meta_rows = [meta_cells[i:i + chunk_size] for i in range(0, len(meta_cells), chunk_size)]
        meta_w = printable_w / chunk_size
        meta_table = Table(meta_rows, colWidths=[meta_w] * chunk_size)
        meta_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#F1F5F9")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 8))

    # 3. Summary KPI Cards (if provided)
    if summary_kpis:
        kpi_cells = []
        for k in summary_kpis:
            lbl = k.get("label", "")
            val = str(k.get("value", ""))
            kpi_p = Paragraph(f"<font color='#64748B' size=7>{lbl}</font><br/><b><font color='#0F2C4A' size=10>{val}</font></b>", cell_style_center)
            kpi_cells.append(kpi_p)

        kpi_table = Table([kpi_cells], colWidths=[printable_w / len(kpi_cells)] * len(kpi_cells))
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFFFF")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#0284C7")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 10))

    # 4. Main Tabular Data
    # Calculate column widths if not explicitly provided
    num_cols = len(headers)
    if not col_widths or len(col_widths) != num_cols:
        col_w = printable_w / num_cols
        col_widths = [col_w] * num_cols
    else:
        # Scale proportionally to fit printable width exactly
        total_given = sum(col_widths)
        if total_given > 0:
            scale = printable_w / total_given
            col_widths = [w * scale for w in col_widths]

    # Alignments map ('L', 'C', 'R')
    if not alignments or len(alignments) != num_cols:
        alignments = ["L"] * num_cols

    # Prepare Header Row with Paragraphs
    head_row = []
    for idx, h in enumerate(headers):
        align = alignments[idx]
        h_style = head_style_right if align == "R" else (head_style_center if align == "C" else head_style_left)
        head_row.append(Paragraph(str(h), h_style))

    # Prepare Data Rows with Paragraphs
    table_data = [head_row]
    for row in rows:
        row_cells = []
        for idx, val in enumerate(row):
            align = alignments[idx] if idx < len(alignments) else "L"
            c_style = cell_style_right if align == "R" else (cell_style_center if align == "C" else cell_style_left)
            
            # Format special status badges
            v_str = str(val if val is not None else "—")
            if align == "C" and v_str in ("P", "Present", "Approved", "Active"):
                v_str = f"<b><font color='#059669'>{v_str}</font></b>"
            elif align == "C" and v_str in ("A", "Absent", "Rejected", "Locked", "MP"):
                v_str = f"<b><font color='#DC2626'>{v_str}</font></b>"
            elif align == "C" and v_str in ("HD", "Half Day", "Pending"):
                v_str = f"<b><font color='#D97706'>{v_str}</font></b>"
            elif align == "C" and v_str in ("OT", "Overtime"):
                v_str = f"<b><font color='#7C3AED'>{v_str}</font></b>"
            elif align == "C" and v_str in ("L", "Leave", "OD", "On Duty"):
                v_str = f"<b><font color='#0284C7'>{v_str}</font></b>"

            row_cells.append(Paragraph(v_str, c_style))
        table_data.append(row_cells)

    main_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    
    # Professional Corporate Table Styling
    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F2C4A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#CBD5E1")),
    ]

    # Alternating row background for readability
    for r_idx in range(1, len(table_data)):
        if r_idx % 2 == 0:
            t_style.append(("BACKGROUND", (0, r_idx), (-1, r_idx), colors.HexColor("#F8FAFC")))
        else:
            t_style.append(("BACKGROUND", (0, r_idx), (-1, r_idx), colors.white))

    main_table.setStyle(TableStyle(t_style))
    story.append(main_table)

    # 5. Signatory Block at bottom of last page
    sig_data = [
        [
            Paragraph("<b>Prepared By:</b><br/><font color='#64748B' size=7.5>HR Operations</font>", cell_style_left),
            Paragraph("<b>Verified By:</b><br/><font color='#64748B' size=7.5>Department Supervisor</font>", cell_style_center),
            Paragraph("<b>Authorized Signatory:</b><br/><font color='#64748B' size=7.5>SBG Factory Management</font>", cell_style_right),
        ],
        [
            Paragraph("<br/><br/>________________________", cell_style_left),
            Paragraph("<br/><br/>________________________", cell_style_center),
            Paragraph("<br/><br/>________________________", cell_style_right),
        ]
    ]
    sig_table = Table(sig_data, colWidths=[printable_w / 3.0] * 3)
    sig_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    
    story.append(Spacer(1, 14))
    story.append(KeepTogether([sig_table]))

    doc.build(story, canvasmaker=NumberedCanvas)
    buf.seek(0)
    return buf


# =============================================================================
# Excel Builder Engine (openpyxl)
# =============================================================================
def build_excel_document(title, metadata, headers, rows, summary_kpis=None, alignments=None):
    """Builds a formatted corporate Excel XLSX workbook."""
    wb = Workbook()
    ws = wb.active
    ws.title = title[:30].replace("/", "-").replace("\\", "-")
    ws.views.sheetView[0].showGridLines = True

    # Styling Tokens
    c_navy = XlFill("solid", fgColor="0F2C4A")
    c_blue = XlFill("solid", fgColor="0284C7")
    c_zebra = XlFill("solid", fgColor="F8FAFC")
    c_kpi = XlFill("solid", fgColor="F1F5F9")
    
    font_title = XlFont(name="Calibri", size=14, bold=True, color="FFFFFF")
    font_sub = XlFont(name="Calibri", size=10, italic=True, color="FFFFFF")
    font_head = XlFont(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_kpi_val = XlFont(name="Calibri", size=11, bold=True, color="0F2C4A")
    font_kpi_lbl = XlFont(name="Calibri", size=9, color="64748B")
    font_cell = XlFont(name="Calibri", size=9.5)
    
    thin = XlSide(style="thin", color="CBD5E1")
    border_all = XlBorder(left=thin, right=thin, top=thin, bottom=thin)

    num_cols = max(len(headers), 6)
    last_col_letter = get_column_letter(num_cols)

    # 1. Title Banner
    ws.merge_cells(f"A1:{last_col_letter}1")
    t_cell = ws["A1"]
    t_cell.value = "SRI BALAJI GEARS (SBG) — SMART ATTENDANCE SYSTEM"
    t_cell.font = font_title
    t_cell.fill = c_navy
    t_cell.alignment = XlAlignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells(f"A2:{last_col_letter}2")
    sub_cell = ws["A2"]
    sub_cell.value = f"{title} | Generated: {now().strftime('%d %b %Y, %I:%M %p')} | Confidential Internal Record"
    sub_cell.font = font_sub
    sub_cell.fill = c_blue
    sub_cell.alignment = XlAlignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20

    cur_row = 4

    # 2. Metadata Rows
    if metadata:
        meta_pairs = []
        for item in metadata:
            meta_pairs.append(f"{item.get('label')}: {item.get('value')}")
        
        ws.cell(row=cur_row, column=1, value="Report Filter Context: " + "  |  ".join(meta_pairs)).font = XlFont(bold=True, size=9.5, color="334155")
        cur_row += 2

    # 3. KPI Summary
    if summary_kpis:
        kpi_col = 1
        for k in summary_kpis:
            lbl = k.get("label", "")
            val = str(k.get("value", ""))
            
            c_top = ws.cell(row=cur_row, column=kpi_col, value=lbl)
            c_top.font = font_kpi_lbl
            c_top.fill = c_kpi
            c_top.border = border_all
            c_top.alignment = XlAlignment(horizontal="center", vertical="center")

            c_bot = ws.cell(row=cur_row + 1, column=kpi_col, value=val)
            c_bot.font = font_kpi_val
            c_bot.fill = c_kpi
            c_bot.border = border_all
            c_bot.alignment = XlAlignment(horizontal="center", vertical="center")
            kpi_col += 1
            if kpi_col > num_cols:
                break
        cur_row += 3

    # 4. Table Header
    header_row_idx = cur_row
    ws.row_dimensions[header_row_idx].height = 24
    for idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=header_row_idx, column=idx, value=str(h))
        cell.font = font_head
        cell.fill = c_navy
        cell.border = border_all
        align = alignments[idx - 1] if alignments and idx - 1 < len(alignments) else "L"
        cell.alignment = XlAlignment(
            horizontal="right" if align == "R" else ("center" if align == "C" else "left"),
            vertical="center",
            wrap_text=True
        )

    # 5. Data Rows
    cur_row += 1
    for r_idx, row in enumerate(rows):
        ws.row_dimensions[cur_row].height = 20
        fill_row = c_zebra if r_idx % 2 == 1 else None
        
        for c_idx, val in enumerate(row, start=1):
            cell = ws.cell(row=cur_row, column=c_idx, value=val)
            cell.font = font_cell
            cell.border = border_all
            if fill_row:
                cell.fill = fill_row
            
            align = alignments[c_idx - 1] if alignments and c_idx - 1 < len(alignments) else "L"
            cell.alignment = XlAlignment(
                horizontal="right" if align == "R" else ("center" if align == "C" else "left"),
                vertical="center"
            )
        cur_row += 1

    # Auto-Fit Column Widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            # Skip title merged banner rows
            if cell.row in (1, 2):
                continue
            v = str(cell.value or "")
            if len(v) > max_len:
                max_len = len(v)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 11)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# =============================================================================
# CSV Builder Engine (UTF-8 with Excel BOM)
# =============================================================================
def build_csv_document(title, metadata, headers, rows):
    """Generates an RFC-4180 compliant CSV file with UTF-8 BOM."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    # Header comments
    writer.writerow([f"# SRI BALAJI GEARS (SBG) - {title}"])
    writer.writerow([f"# Generated: {now().strftime('%Y-%m-%d %H:%M:%S IST')}"])
    if metadata:
        meta_str = " | ".join(f"{item.get('label')}: {item.get('value')}" for item in metadata)
        writer.writerow([f"# Context: {meta_str}"])
    writer.writerow([])  # blank row

    # Headers and rows
    writer.writerow(headers)
    for row in rows:
        writer.writerow([str(v) if v is not None else "" for v in row])

    # Convert to bytes with UTF-8 BOM
    raw_bytes = buf.getvalue().encode("utf-8-sig")
    return io.BytesIO(raw_bytes)


# =============================================================================
# Word (DOCX) Document Builder Engine (python-docx)
# =============================================================================
def build_docx_document(title, metadata, headers, rows, summary_kpis=None,
                        orientation="portrait", alignments=None, logo_path=None):
    """Generates a professional corporate Word document (.docx)."""
    doc = docx.Document()

    # Section page setup
    section = doc.sections[0]
    if orientation.lower() == "landscape":
        section.orientation = docx.enum.section.WD_ORIENT.LANDSCAPE
        section.page_width = Inches(11.69)
        section.page_height = Inches(8.27)
    else:
        section.orientation = docx.enum.section.WD_ORIENT.PORTRAIT
        section.page_width = Inches(8.27)
        section.page_height = Inches(11.69)

    section.top_margin = Inches(0.5)
    section.bottom_margin = Inches(0.5)
    section.left_margin = Inches(0.5)
    section.right_margin = Inches(0.5)

    # 1. Header Table (Logo + Title)
    head_table = doc.add_table(rows=1, cols=2)
    head_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    head_table.autofit = False

    # Logo in left cell if available
    cell_logo = head_table.cell(0, 0)
    cell_logo.width = Inches(1.0)
    if logo_path and os.path.exists(logo_path):
        try:
            p_logo = cell_logo.paragraphs[0]
            p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_logo.add_run().add_picture(logo_path, width=Inches(0.75), height=Inches(0.75))
        except Exception:
            pass

    # Title in right cell
    cell_title = head_table.cell(0, 1)
    cell_title.width = Inches(9.5 if orientation == "landscape" else 6.2)
    p_t = cell_title.paragraphs[0]
    r_t1 = p_t.add_run("SRI BALAJI GEARS (SBG)\n")
    r_t1.bold = True
    r_t1.font.size = Pt(14)
    r_t1.font.color.rgb = RGBColor(15, 44, 74)

    r_t2 = p_t.add_run(f"{title}\n")
    r_t2.bold = True
    r_t2.font.size = Pt(11)
    r_t2.font.color.rgb = RGBColor(2, 132, 199)

    r_t3 = p_t.add_run(f"Generated: {now().strftime('%d %B %Y, %I:%M %p')} · Confidential Internal Record")
    r_t3.font.size = Pt(8.5)
    r_t3.font.italic = True
    r_t3.font.color.rgb = RGBColor(100, 116, 139)

    doc.add_paragraph()  # spacing

    # 2. Metadata Box
    if metadata:
        meta_table = doc.add_table(rows=1, cols=len(metadata))
        meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for idx, item in enumerate(metadata):
            c = meta_table.cell(0, idx)
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r_lbl = p.add_run(f"{item.get('label')}\n")
            r_lbl.font.size = Pt(7.5)
            r_lbl.font.color.rgb = RGBColor(100, 116, 139)
            r_val = p.add_run(str(item.get("value")))
            r_val.bold = True
            r_val.font.size = Pt(9)
            r_val.font.color.rgb = RGBColor(15, 23, 42)
        doc.add_paragraph()

    # 3. Main Data Table
    table = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"

    # Header Row
    hdr_cells = table.rows[0].cells
    for i, title_text in enumerate(headers):
        hdr_cells[i].text = str(title_text)
        p = hdr_cells[i].paragraphs[0]
        p.runs[0].bold = True
        p.runs[0].font.size = Pt(8.5)
        p.runs[0].font.color.rgb = RGBColor(255, 255, 255)
        align = alignments[i] if alignments and i < len(alignments) else "L"
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if align == "R" else (WD_ALIGN_PARAGRAPH.CENTER if align == "C" else WD_ALIGN_PARAGRAPH.LEFT)

        # Navy background for header cell
        shading_xml = f'<w:shd {docx.oxml.ns.nsdecls("w")} w:fill="0F2C4A"/>'
        hdr_cells[i]._tc.get_or_add_tcPr().append(docx.oxml.parse_xml(shading_xml))

    # Data Rows
    for r_idx, row_data in enumerate(rows):
        row_cells = table.rows[r_idx + 1].cells
        is_even = r_idx % 2 == 1
        for c_idx, val in enumerate(row_data):
            row_cells[c_idx].text = str(val if val is not None else "—")
            p = row_cells[c_idx].paragraphs[0]
            p.runs[0].font.size = Pt(8)
            p.runs[0].font.color.rgb = RGBColor(15, 23, 42)
            align = alignments[c_idx] if alignments and c_idx < len(alignments) else "L"
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if align == "R" else (WD_ALIGN_PARAGRAPH.CENTER if align == "C" else WD_ALIGN_PARAGRAPH.LEFT)

            if is_even:
                shd = f'<w:shd {docx.oxml.ns.nsdecls("w")} w:fill="F8FAFC"/>'
                row_cells[c_idx]._tc.get_or_add_tcPr().append(docx.oxml.parse_xml(shd))

    # Document Footer
    doc.add_paragraph()
    p_foot = doc.add_paragraph()
    p_foot.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r_foot = p_foot.add_run("Authorized Signatory: ________________________\nSBG Factory Operations Management")
    r_foot.font.size = Pt(8.5)
    r_foot.font.color.rgb = RGBColor(100, 116, 139)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# =============================================================================
# Business Data Fetchers for all 5 Report Modules
# =============================================================================

def fetch_attendance_report(db, start_date, end_date, emp_id=None, dept=None,
                            status=None, category_id=None):
    """Fetches attendance records and prepares clean tabular export rows."""
    params = [start_date, end_date]
    conds = ["a.date >= ? AND a.date <= ?"]

    if emp_id:
        conds.append("a.emp_id = ?")
        params.append(emp_id)
    if dept:
        conds.append("e.department = ?")
        params.append(dept)
    if status:
        conds.append("a.status = ?")
        params.append(status)
    if category_id:
        conds.append("e.category_id = ?")
        params.append(category_id)

    sql = f"""
        SELECT a.date, e.emp_code, e.name as emp_name, e.department,
               s.name as shift_name, c.name as category_name,
               a.check_in, a.check_out, a.work_hours, a.ot_hours,
               a.status, a.late, a.remarks
        FROM attendance a
        JOIN employees e ON e.id = a.emp_id
        LEFT JOIN shifts s ON s.id = a.shift_id
        LEFT JOIN employee_categories c ON c.id = e.category_id
        WHERE {" AND ".join(conds)}
        ORDER BY a.date DESC, e.emp_code ASC
        LIMIT 2500
    """
    rows_db = db.execute(sql, params).fetchall()

    headers = [
        "Date", "Emp ID", "Employee Name", "Department", "Category",
        "Shift", "Check In", "Check Out", "Work Hrs", "OT Hrs", "Status", "Late"
    ]
    alignments = ["C", "C", "L", "L", "L", "L", "C", "C", "R", "R", "C", "C"]
    col_widths = [65, 55, 120, 85, 75, 75, 50, 50, 50, 45, 55, 45]

    data_rows = []
    tot_work = 0.0
    tot_ot = 0.0
    status_counts = {"P": 0, "OT": 0, "HD": 0, "A": 0, "MP": 0}

    for r in rows_db:
        w_h = float(r["work_hours"] or 0.0)
        ot_h = float(r["ot_hours"] or 0.0)
        tot_work += w_h
        tot_ot += ot_h
        st = r["status"] or "P"
        if st in status_counts:
            status_counts[st] += 1

        in_fmt = r["check_in"].split()[1][:5] if r["check_in"] and " " in r["check_in"] else (r["check_in"] or "—")
        out_fmt = r["check_out"].split()[1][:5] if r["check_out"] and " " in r["check_out"] else (r["check_out"] or "—")

        data_rows.append([
            r["date"],
            r["emp_code"],
            r["emp_name"],
            r["department"] or "General",
            r["category_name"] or "Staff",
            r["shift_name"] or "Standard Shift",
            in_fmt,
            out_fmt,
            f"{w_h:.1f}h",
            f"{ot_h:.1f}h",
            r["status"],
            "Late" if r["late"] else "On Time"
        ])

    kpis = [
        {"label": "Total Punches", "value": len(data_rows)},
        {"label": "Present (P/OT)", "value": status_counts["P"] + status_counts["OT"]},
        {"label": "Half Day (HD)", "value": status_counts["HD"]},
        {"label": "Missed (MP)", "value": status_counts["MP"]},
        {"label": "Total Hours", "value": f"{tot_work:.1f}h"},
        {"label": "Total OT Hours", "value": f"{tot_ot:.1f}h"},
    ]

    return {
        "title": f"Attendance Register Report ({start_date} to {end_date})",
        "headers": headers,
        "rows": data_rows,
        "alignments": alignments,
        "col_widths": col_widths,
        "summary_kpis": kpis,
        "total_records": len(data_rows),
    }


def fetch_employee_details_report(db, dept=None, category_id=None, active_only=True):
    """Fetches master employee list and details."""
    conds = []
    params = []

    if active_only:
        conds.append("e.active = 1")
    if dept:
        conds.append("e.department = ?")
        params.append(dept)
    if category_id:
        conds.append("e.category_id = ?")
        params.append(category_id)

    where_clause = f"WHERE {' AND '.join(conds)}" if conds else ""
    sql = f"""
        SELECT e.*, s.name as shift_name, c.name as category_name, c.code as category_code
        FROM employees e
        LEFT JOIN shifts s ON s.id = e.shift_id
        LEFT JOIN employee_categories c ON c.id = e.category_id
        {where_clause}
        ORDER BY e.emp_code ASC
    """
    rows_db = db.execute(sql, params).fetchall()

    headers = [
        "Emp ID", "Full Name", "Department", "Designation",
        "Workforce Category", "Assigned Shift", "Mobile Phone",
        "Face ID Status", "Device Status", "Account Status"
    ]
    alignments = ["C", "L", "L", "L", "L", "L", "C", "C", "C", "C"]
    col_widths = [55, 120, 85, 80, 85, 85, 75, 65, 60, 60]

    data_rows = []
    face_enrolled = 0
    active_count = 0

    for r in rows_db:
        if r["face_registered"]:
            face_enrolled += 1
        if r["active"]:
            active_count += 1

        data_rows.append([
            r["emp_code"],
            r["name"],
            r["department"] or "General",
            r["designation"] or "Staff",
            r["category_name"] or "Staff",
            r["shift_name"] or "Standard Shift",
            r["phone"] or "—",
            "Enrolled" if r["face_registered"] else "Pending",
            "Linked" if r["device_registered"] else "Not Linked",
            "Active" if r["active"] else "Blocked"
        ])

    kpis = [
        {"label": "Total Workforce", "value": len(data_rows)},
        {"label": "Active Accounts", "value": active_count},
        {"label": "Face ID Enrolled", "value": face_enrolled},
        {"label": "Pending Setup", "value": len(data_rows) - face_enrolled},
    ]

    return {
        "title": "Master Employee Workforce Directory",
        "headers": headers,
        "rows": data_rows,
        "alignments": alignments,
        "col_widths": col_widths,
        "summary_kpis": kpis,
        "total_records": len(data_rows),
    }


def fetch_salary_report(db, month_str, dept=None, category_id=None):
    """Fetches payroll register for specified month."""
    conds = ["p.month = ?"]
    params = [month_str]

    if dept:
        conds.append("e.department = ?")
        params.append(dept)
    if category_id:
        conds.append("e.category_id = ?")
        params.append(category_id)

    sql = f"""
        SELECT p.*, e.emp_code, e.name as emp_name, e.department,
               c.name as category_name
        FROM monthly_payroll p
        JOIN employees e ON e.id = p.emp_id
        LEFT JOIN employee_categories c ON c.id = e.category_id
        WHERE {" AND ".join(conds)}
        ORDER BY e.emp_code ASC
    """
    rows_db = db.execute(sql, params).fetchall()

    headers = [
        "Emp ID", "Employee Name", "Department", "Category",
        "Present Days", "Basic Pay", "OT Pay", "Bonus/Allow",
        "Gross Pay", "Total Ded.", "Net Salary", "Status"
    ]
    alignments = ["C", "L", "L", "L", "C", "R", "R", "R", "R", "R", "R", "C"]
    col_widths = [55, 120, 80, 80, 55, 60, 55, 55, 65, 55, 70, 55]

    data_rows = []
    tot_gross = 0.0
    tot_net = 0.0
    tot_ded = 0.0

    for r in rows_db:
        gross = float(r["gross_salary"] or 0.0)
        net = float(r["net_salary"] or 0.0)
        late_ded = float(r["late_deduction"] or 0.0)
        unpaid_ded = float(r["unpaid_leave_deduction"] or 0.0)
        other_ded = float(r["other_deductions"] or 0.0)
        ded = late_ded + unpaid_ded + other_ded

        tot_gross += gross
        tot_net += net
        tot_ded += ded

        data_rows.append([
            r["emp_code"],
            r["emp_name"],
            r["department"] or "General",
            r["category_name"] or "Staff",
            f"{r['present_days'] or 0} d",
            f"₹{float(r['basic_salary'] or 0.0):,.0f}",
            f"₹{float(r['ot_pay'] or 0.0):,.0f}",
            f"₹{float(r['bonus_allowance'] or 0.0):,.0f}",
            f"₹{gross:,.0f}",
            f"₹{ded:,.0f}",
            f"₹{net:,.0f}",
            r["status"] or "Calculated"
        ])

    kpis = [
        {"label": "Employees in Payroll", "value": len(data_rows)},
        {"label": "Total Gross Pay", "value": f"₹{tot_gross:,.0f}"},
        {"label": "Total Deductions", "value": f"₹{tot_ded:,.0f}"},
        {"label": "Total Net Payable", "value": f"₹{tot_net:,.0f}"},
    ]

    return {
        "title": f"Monthly Salary & Payroll Register ({month_str})",
        "headers": headers,
        "rows": data_rows,
        "alignments": alignments,
        "col_widths": col_widths,
        "summary_kpis": kpis,
        "total_records": len(data_rows),
    }


def fetch_leave_report(db, start_date, end_date, emp_id=None, status=None):
    """Fetches leave requests and approval audit records."""
    conds = ["l.from_date <= ? AND l.to_date >= ?"]
    params = [end_date, start_date]

    if emp_id:
        conds.append("l.emp_id = ?")
        params.append(emp_id)
    if status:
        conds.append("l.status = ?")
        params.append(status)

    sql = f"""
        SELECT l.*, e.emp_code, e.name as emp_name, e.department,
               t.name as leave_type_name, t.code as leave_type_code,
               dec.name as decided_by_name
        FROM leaves l
        JOIN employees e ON e.id = l.emp_id
        JOIN leave_types t ON t.id = l.type_id
        LEFT JOIN employees dec ON dec.id = l.decided_by
        WHERE {" AND ".join(conds)}
        ORDER BY l.id DESC
    """
    rows_db = db.execute(sql, params).fetchall()

    headers = [
        "Req ID", "Emp ID", "Employee Name", "Department",
        "Leave Type", "From Date", "To Date", "Days",
        "Reason", "Status", "Decided By"
    ]
    alignments = ["C", "C", "L", "L", "C", "C", "C", "C", "L", "C", "L"]
    col_widths = [45, 55, 115, 80, 70, 60, 60, 45, 115, 60, 65]

    data_rows = []
    appr_cnt = 0
    pend_cnt = 0
    rej_cnt = 0
    tot_days = 0.0

    for r in rows_db:
        st = r["status"]
        if st == "Approved":
            appr_cnt += 1
        elif st == "Pending":
            pend_cnt += 1
        elif st == "Rejected":
            rej_cnt += 1

        d_cnt = float(r["days"] or 1.0)
        tot_days += d_cnt

        data_rows.append([
            f"#{r['id']}",
            r["emp_code"],
            r["emp_name"],
            r["department"] or "General",
            r["leave_type_code"],
            r["from_date"],
            r["to_date"],
            f"{d_cnt:.1f}",
            (r["reason"] or "")[:40],
            r["status"],
            r["decided_by_name"] or "—"
        ])

    kpis = [
        {"label": "Total Requests", "value": len(data_rows)},
        {"label": "Approved", "value": appr_cnt},
        {"label": "Pending", "value": pend_cnt},
        {"label": "Rejected", "value": rej_cnt},
        {"label": "Total Days", "value": f"{tot_days:.1f}"},
    ]

    return {
        "title": f"Leave Requests & Approvals Report ({start_date} to {end_date})",
        "headers": headers,
        "rows": data_rows,
        "alignments": alignments,
        "col_widths": col_widths,
        "summary_kpis": kpis,
        "total_records": len(data_rows),
    }


def fetch_monthly_summary_report(db, month_str, category_id=None, dept=None):
    """Fetches muster roll summary for monthly overview."""
    from .services import build_register, month_range
    try:
        y, m = map(int, month_str.split("-"))
        start, end = month_range(y, m)
    except Exception:
        t = today()
        start, end = month_range(t.year, t.month)

    conds = ["e.active = 1"]
    params = []
    if category_id:
        conds.append("e.category_id = ?")
        params.append(category_id)
    if dept:
        conds.append("e.department = ?")
        params.append(dept)

    emps_db = db.execute(
        f"SELECT e.*, c.name as category_name, c.work_hours as category_work_hours "
        f"FROM employees e LEFT JOIN employee_categories c ON c.id=e.category_id "
        f"WHERE {' AND '.join(conds)} ORDER BY e.emp_code ASC",
        params
    ).fetchall()
    emps = [dict(e) for e in emps_db]

    reg = build_register(emps, start, end)

    headers = [
        "Emp ID", "Employee Name", "Department", "Category",
        "Days Come", "Present (P)", "Overtime (OT)", "Half Day (HD)",
        "Absent (A)", "Leave", "On Duty", "Work Hrs", "OT Hrs", "Payable"
    ]
    alignments = ["C", "L", "L", "L", "C", "C", "C", "C", "C", "C", "C", "R", "R", "C"]
    col_widths = [50, 110, 75, 75, 55, 45, 45, 45, 45, 45, 45, 50, 45, 45]

    data_rows = []
    total_payable = 0.0
    total_ot_hours = 0.0

    for e in emps:
        s = reg[e["id"]]["summary"]
        days_come = s["P"] + s["OT"] + s["HD"] + s["MP"] + s["IN"]
        total_payable += float(s["payable"] or 0.0)
        total_ot_hours += float(s["ot_hours"] or 0.0)

        data_rows.append([
            e["emp_code"],
            e["name"],
            e["department"] or "General",
            e["category_name"] or "Staff",
            str(days_come),
            str(s["P"]),
            str(s["OT"]),
            str(s["HD"]),
            str(s["A"]),
            str(s["L"] + 0.5 * s["HL"]),
            str(s["OD"]),
            f"{s['hours']}h",
            f"{s['ot_hours']}h",
            str(s["payable"])
        ])

    kpis = [
        {"label": "Staff Count", "value": len(data_rows)},
        {"label": "Month Period", "value": start.strftime("%B %Y")},
        {"label": "Total Payable Days", "value": f"{total_payable:.1f}"},
        {"label": "Total OT Hours", "value": f"{total_ot_hours:.1f}h"},
    ]

    return {
        "title": f"Muster Roll Monthly Attendance Summary ({start.strftime('%B %Y')})",
        "headers": headers,
        "rows": data_rows,
        "alignments": alignments,
        "col_widths": col_widths,
        "summary_kpis": kpis,
        "total_records": len(data_rows),
    }
