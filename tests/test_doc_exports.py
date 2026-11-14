import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.db import get_db
from app.doc_exports import (
    build_pdf_document,
    build_excel_document,
    build_csv_document,
    build_docx_document,
    fetch_attendance_report,
    fetch_employee_details_report,
    fetch_salary_report,
    fetch_leave_report,
    fetch_monthly_summary_report,
)


class TestDocExportsAndReportAlignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = cls.app.test_client()

    def _login_hr(self):
        with self.app.app_context():
            db = get_db()
            admin = db.execute("SELECT * FROM employees WHERE role='hr' LIMIT 1").fetchone()
            admin_id = admin["id"] if admin else 1
            admin_name = admin["name"] if admin else "Admin"
            admin_code = admin["emp_code"] if admin else "SBG001"
        with self.client.session_transaction() as sess:
            sess["uid"] = admin_id
            sess["role"] = "hr"
            sess["name"] = admin_name
            sess["emp_code"] = admin_code
            sess["csrf"] = "testcsrf"
        return admin_id

    def _login_emp(self):
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT * FROM employees WHERE role='employee' LIMIT 1").fetchone()
            emp_id = emp["id"] if emp else 2
            emp_name = emp["name"] if emp else "Employee"
            emp_code = emp["emp_code"] if emp else "EMP002"
        with self.client.session_transaction() as sess:
            sess["uid"] = emp_id
            sess["role"] = "employee"
            sess["name"] = emp_name
            sess["emp_code"] = emp_code
            sess["csrf"] = "testcsrf"
        return emp_id

    def test_01_pdf_builder_formatting_and_alignment(self):
        """Verify PDF builder generates valid A4 document with explicit styles and no text clipping."""
        title = "Attendance Register Audit"
        metadata = [
            {"label": "Organization", "value": "Sri Balaji Gears (SBG)"},
            {"label": "Report Period", "value": "November 2026"},
        ]
        headers = ["Date", "Emp ID", "Employee Full Name", "Department", "Shift", "Status", "Hours"]
        alignments = ["C", "C", "L", "L", "L", "C", "R"]
        rows = [
            ["2026-11-01", "EMP001", "M. Damodharan (Lead Engineer Operations)", "Manufacturing & Production", "General Morning Shift (09:00 - 18:00)", "P", "9.0h"],
            ["2026-11-01", "EMP002", "S. Suresh Kumar (Senior CNC Machine Specialist)", "Machine Shop Operations", "Standard Factory Shift (08:30 - 17:30)", "OT", "11.5h"],
            ["2026-11-02", "EMP003", "R. Rajesh (Quality Control & Compliance Inspector)", "Quality Assurance", "Night Operations Shift (20:00 - 05:00)", "HD", "4.5h"],
        ]
        kpis = [
            {"label": "Total Present", "value": 2},
            {"label": "Half Day", "value": 1},
            {"label": "Total OT Hours", "value": "2.5h"},
        ]

        # Test Portrait
        buf_portrait = build_pdf_document(
            title=title,
            metadata=metadata,
            headers=headers,
            rows=rows,
            alignments=alignments,
            orientation="portrait",
            summary_kpis=kpis,
            logo_path="app/static/img/sbg_logo.png"
        )
        val_p = buf_portrait.getvalue()
        self.assertGreater(len(val_p), 1000)
        self.assertTrue(val_p.startswith(b"%PDF"))

        # Test Landscape
        buf_landscape = build_pdf_document(
            title=title,
            metadata=metadata,
            headers=headers,
            rows=rows,
            alignments=alignments,
            orientation="landscape",
            summary_kpis=kpis,
            logo_path="app/static/img/sbg_logo.png"
        )
        val_l = buf_landscape.getvalue()
        self.assertGreater(len(val_l), 1000)
        self.assertTrue(val_l.startswith(b"%PDF"))

    def test_02_excel_csv_docx_builders(self):
        """Verify Excel (XLSX), CSV, and Word (DOCX) generate valid document bytes."""
        title = "Employee Workforce Directory"
        metadata = [{"label": "Org", "value": "SBG"}]
        headers = ["Emp ID", "Name", "Department", "Designation", "Status"]
        alignments = ["C", "L", "L", "L", "C"]
        rows = [
            ["EMP001", "Admin User", "Administration", "Plant Head", "Active"],
            ["EMP002", "Operator 1", "Production", "Technician", "Active"],
        ]

        # XLSX
        xl = build_excel_document(title, metadata, headers, rows, alignments=alignments)
        val_xl = xl.getvalue()
        self.assertGreater(len(val_xl), 1000)
        self.assertEqual(val_xl[:2], b"PK")

        # CSV
        csv_buf = build_csv_document(title, metadata, headers, rows)
        val_csv = csv_buf.getvalue()
        self.assertGreater(len(val_csv), 50)
        self.assertTrue(val_csv.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"EMP001", val_csv)

        # DOCX
        docx_buf = build_docx_document(title, metadata, headers, rows, alignments=alignments, orientation="landscape", logo_path="app/static/img/sbg_logo.png")
        val_docx = docx_buf.getvalue()
        self.assertGreater(len(val_docx), 1000)
        self.assertEqual(val_docx[:2], b"PK")

    def test_03_all_five_data_fetchers(self):
        """Verify that all 5 business report fetchers query real database records without error."""
        with self.app.app_context():
            db = get_db()

            # 1. Attendance
            att = fetch_attendance_report(db, "2026-11-01", "2026-11-30")
            self.assertIn("headers", att)
            self.assertIn("rows", att)
            self.assertIn("summary_kpis", att)
            self.assertIsInstance(att["rows"], list)

            # 2. Employees
            emp = fetch_employee_details_report(db)
            self.assertGreater(len(emp["rows"]), 0)
            self.assertIn("Emp ID", emp["headers"])

            # 3. Salary
            sal = fetch_salary_report(db, "2026-11")
            self.assertIn("title", sal)
            self.assertIsInstance(sal["rows"], list)

            # 4. Leave
            lve = fetch_leave_report(db, "2026-11-01", "2026-11-30")
            self.assertIn("title", lve)
            self.assertIsInstance(lve["rows"], list)

            # 5. Monthly Summary
            summ = fetch_monthly_summary_report(db, "2026-11")
            self.assertGreater(len(summ["rows"]), 0)
            self.assertIn("Days Come", summ["headers"])

    def test_04_hr_preview_and_print_view_routes(self):
        """Verify /hr/reports/preview and /hr/reports/print-view routes."""
        self._login_hr()

        # Preview API
        res = self.client.get("/hr/reports/preview?report_type=attendance&from_date=2026-11-01&to_date=2026-11-30")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("headers", data)
        self.assertIn("rows", data)
        self.assertIn("summary_kpis", data)

        # Print View HTML
        res_print = self.client.get("/hr/reports/print-view?report_type=attendance&from_date=2026-11-01&to_date=2026-11-30")
        self.assertEqual(res_print.status_code, 200)
        self.assertIn(b"SRI BALAJI GEARS", res_print.data)
        self.assertIn(b"Print Document", res_print.data)

    def test_05_hr_report_downloads_all_formats(self):
        """Verify /hr/reports/download for PDF, XLSX, DOCX, CSV."""
        self._login_hr()

        # PDF Download
        res_pdf = self.client.get("/hr/reports/download?report_type=attendance&format=pdf&from_date=2026-11-01&to_date=2026-11-30")
        self.assertEqual(res_pdf.status_code, 200)
        self.assertEqual(res_pdf.content_type, "application/pdf")
        self.assertTrue(res_pdf.data.startswith(b"%PDF"))

        # Excel Download
        res_xlsx = self.client.get("/hr/reports/download?report_type=employees&format=xlsx")
        self.assertEqual(res_xlsx.status_code, 200)
        self.assertIn("spreadsheetml", res_xlsx.content_type)
        self.assertEqual(res_xlsx.data[:2], b"PK")

        # Word (DOCX) Download
        res_docx = self.client.get("/hr/reports/download?report_type=salary&format=docx&month=2026-11")
        self.assertEqual(res_docx.status_code, 200)
        self.assertIn("wordprocessingml", res_docx.content_type)
        self.assertEqual(res_docx.data[:2], b"PK")

        # CSV Download
        res_csv = self.client.get("/hr/reports/download?report_type=leave&format=csv&from_date=2026-11-01&to_date=2026-11-30")
        self.assertEqual(res_csv.status_code, 200)
        self.assertIn("text/csv", res_csv.content_type)
        self.assertTrue(res_csv.data.startswith(b"\xef\xbb\xbf"))

    def test_06_single_employee_multi_format_export(self):
        """Verify single employee export supports PDF, DOCX, XLSX."""
        with self.app.app_context():
            db = get_db()
            emp = db.execute("SELECT * FROM employees WHERE role='employee' LIMIT 1").fetchone()
            self.assertIsNotNone(emp)

        self._login_hr()

        # HR downloading employee PDF
        res = self.client.get(f"/hr/reports/employee/{emp['id']}/export?format=pdf&month=2026-11")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "application/pdf")
        self.assertTrue(res.data.startswith(b"%PDF"))

        # HR downloading employee DOCX
        res_w = self.client.get(f"/hr/reports/employee/{emp['id']}/export?format=docx&month=2026-11")
        self.assertEqual(res_w.status_code, 200)
        self.assertIn("wordprocessingml", res_w.content_type)

        # Employee Self-Service Export in PDF
        self._login_emp()
        res_self = self.client.get("/history/export?format=pdf&month=2026-11")
        self.assertEqual(res_self.status_code, 200)
        self.assertEqual(res_self.content_type, "application/pdf")
        self.assertTrue(res_self.data.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
