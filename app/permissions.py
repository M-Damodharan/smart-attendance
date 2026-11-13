"""Role-Based User & Page Permission Management System for SBG Smart Attendance.

Provides:
- Complete module and page catalogue across HR and Employee portals.
- 5 Granular permission flags per module: View, Add, Edit, Delete, Export.
- Role defaults (HR vs Employee) with backward-compatibility for legacy accounts.
- Super Admin safeguards: Admin (HR001 / ADMIN_CODE) always retains 100% full access.
- Backend authorization decorators (@permission_required) for routes and APIs.
- Comprehensive audit trail for all permission modifications.
"""
import os
import json
import logging
from functools import wraps
from datetime import datetime
from flask import abort, current_app, flash, g, has_request_context, jsonify, redirect, request, session, url_for

logger = logging.getLogger(__name__)

# Master list of all permission actions
ALL_ACTIONS = ["view", "add", "edit", "delete", "export"]

# Master definition of all Application Modules and Pages
MODULE_GROUPS = [
    {
        "group_id": "admin_hr_dashboard",
        "group_name": "Admin & HR Dashboard",
        "icon": "dashboard",
        "modules": [
            {
                "key": "hr_dashboard",
                "name": "HR Dashboard",
                "description": "Main HR overview, live attendance stats, and quick actions",
                "endpoint": "hr.dashboard",
                "url": "/hr/dashboard",
                "actions": ["view"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_powerbi",
                "name": "Power BI Analytics",
                "description": "Interactive Power BI attendance and workforce analytics",
                "endpoint": "hr.powerbi_dashboard",
                "url": "/hr/powerbi-dashboard",
                "actions": ["view", "export"],
                "default_roles": ["hr"],
            },
        ],
    },
    {
        "group_id": "attendance_operations",
        "group_name": "Attendance & Operations",
        "icon": "clock",
        "modules": [
            {
                "key": "hr_attendance",
                "name": "Attendance Register",
                "description": "Live daily attendance status, check-in/out records, and geo-details",
                "endpoint": "hr.attendance",
                "url": "/hr/attendance",
                "actions": ["view", "edit", "export"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_sunday_working",
                "name": "Sunday Duty Management",
                "description": "Schedule, assign, approve, and record Sunday duty shifts",
                "endpoint": "hr.sunday_working",
                "url": "/hr/sunday-working",
                "actions": ["view", "add", "edit", "export"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_late_coming",
                "name": "Late Coming Requests",
                "description": "Review and decide employee late arrival explanations",
                "endpoint": "hr.late_coming",
                "url": "/hr/late-coming",
                "actions": ["view", "edit"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_missed_punches",
                "name": "Missed Punches",
                "description": "Resolve single-punch and missed punch records",
                "endpoint": "hr.missed_punches",
                "url": "/hr/missed-punches",
                "actions": ["view", "edit"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_corrections",
                "name": "Attendance Corrections",
                "description": "Manual attendance entry and punch adjustments",
                "endpoint": "hr.corrections",
                "url": "/hr/corrections",
                "actions": ["view", "add", "edit"],
                "default_roles": ["hr"],
            },
        ],
    },
    {
        "group_id": "workforce_shifts",
        "group_name": "Workforce & Shifts",
        "icon": "users",
        "modules": [
            {
                "key": "hr_employees",
                "name": "Employee Directory",
                "description": "Manage employee master records, biometric status, and credentials",
                "endpoint": "hr.employees",
                "url": "/hr/employees",
                "actions": ["view", "add", "edit", "delete", "export"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_shifts",
                "name": "Shift Management",
                "description": "Define shift timings, grace periods, and weekly schedules",
                "endpoint": "hr.shifts",
                "url": "/hr/shifts",
                "actions": ["view", "add", "edit"],
                "default_roles": ["hr"],
            },
        ],
    },
    {
        "group_id": "approvals_hub",
        "group_name": "Approvals & Requests",
        "icon": "check-circle",
        "modules": [
            {
                "key": "hr_approvals",
                "name": "Approvals Hub",
                "description": "Approve or reject leave applications, OD requests, and permission slips",
                "endpoint": "hr.approvals",
                "url": "/hr/approvals",
                "actions": ["view", "edit"],
                "default_roles": ["hr"],
            },
        ],
    },
    {
        "group_id": "payroll_mgmt",
        "group_name": "Payroll Management",
        "icon": "dollar-sign",
        "modules": [
            {
                "key": "hr_payroll",
                "name": "Payroll Processing",
                "description": "Monthly payroll calculation, salary adjustments, and approval",
                "endpoint": "hr.payroll",
                "url": "/hr/payroll",
                "actions": ["view", "add", "edit", "export"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_payroll_setup",
                "name": "Salary Profiles Setup",
                "description": "Configure base salaries, allowances, bank accounts, and OT rates",
                "endpoint": "hr.payroll_setup",
                "url": "/hr/payroll/setup",
                "actions": ["view", "edit"],
                "default_roles": ["hr"],
            },
        ],
    },
    {
        "group_id": "reports_exports",
        "group_name": "Reports & Intelligence",
        "icon": "file-text",
        "modules": [
            {
                "key": "hr_reports",
                "name": "Reports & Analytics",
                "description": "Monthly summary, muster roll, and individual attendance exports",
                "endpoint": "hr.reports",
                "url": "/hr/reports",
                "actions": ["view", "export"],
                "default_roles": ["hr"],
            },
        ],
    },
    {
        "group_id": "system_security",
        "group_name": "System Administration & Security",
        "icon": "shield",
        "modules": [
            {
                "key": "hr_settings",
                "name": "System Settings",
                "description": "Geofencing, GPS accuracy, AI threshold calibration, and office locations",
                "endpoint": "hr.settings",
                "url": "/hr/settings",
                "actions": ["view", "edit"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_permissions",
                "name": "User & Page Permissions",
                "description": "Role-based page and action permission management",
                "endpoint": "hr.permissions",
                "url": "/hr/permissions",
                "actions": ["view", "edit"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_face_audit",
                "name": "Face Security Audit",
                "description": "AI-detected buddy punch alerts and face verification logs",
                "endpoint": "hr.face_security_audit",
                "url": "/hr/face-security-audit",
                "actions": ["view", "edit"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_audit",
                "name": "System Audit Trail",
                "description": "System-wide activity logs, login trails, and security changes",
                "endpoint": "hr.audit_log",
                "url": "/hr/audit",
                "actions": ["view", "export"],
                "default_roles": ["hr"],
            },
            {
                "key": "hr_cloud_backup",
                "name": "Cloud Sync & Backups",
                "description": "Automatic and manual snapshots, restore points, and sqlite downloads",
                "endpoint": "hr.cloud_backup_sync",
                "url": "/hr/cloud-backup-sync",
                "actions": ["view", "add", "edit", "export"],
                "default_roles": ["hr"],
            },
        ],
    },
    {
        "group_id": "employee_portal",
        "group_name": "Employee Self-Service (ESS)",
        "icon": "user",
        "modules": [
            {
                "key": "emp_dashboard",
                "name": "Employee Home",
                "description": "Personal shift timing, today's status, and quick punch overview",
                "endpoint": "emp.dashboard",
                "url": "/dashboard",
                "actions": ["view"],
                "default_roles": ["employee", "hr"],
            },
            {
                "key": "emp_punch",
                "name": "Punch Attendance",
                "description": "Biometric face verification and GPS geofence attendance marking",
                "endpoint": "emp.punch",
                "url": "/punch",
                "actions": ["view", "add"],
                "default_roles": ["employee", "hr"],
            },
            {
                "key": "emp_history",
                "name": "Attendance History",
                "description": "Past attendance logs, work hours, overtime, and monthly export",
                "endpoint": "emp.history",
                "url": "/history",
                "actions": ["view", "export"],
                "default_roles": ["employee", "hr"],
            },
            {
                "key": "emp_salary",
                "name": "My Salary & Payslips",
                "description": "View monthly payslips, basic wage, deductions, and net pay",
                "endpoint": "emp.my_salary",
                "url": "/my-salary",
                "actions": ["view", "export"],
                "default_roles": ["employee", "hr"],
            },
            {
                "key": "emp_leave",
                "name": "Leave Applications",
                "description": "Apply for casual, sick, or privilege leaves and track status",
                "endpoint": "emp.leave",
                "url": "/leave",
                "actions": ["view", "add"],
                "default_roles": ["employee", "hr"],
            },
            {
                "key": "emp_od",
                "name": "On Duty (OD) Requests",
                "description": "Submit and monitor on-duty client or field work requests",
                "endpoint": "emp.od",
                "url": "/od",
                "actions": ["view", "add"],
                "default_roles": ["employee", "hr"],
            },
            {
                "key": "emp_permission",
                "name": "Permission Slips",
                "description": "Short permissions for late coming, early leaving, or official duties",
                "endpoint": "emp.permission",
                "url": "/permission",
                "actions": ["view", "add"],
                "default_roles": ["employee", "hr"],
            },
            {
                "key": "emp_late_submit",
                "name": "Late Coming Explanation",
                "description": "Submit justifications for late clock-ins",
                "endpoint": "emp.late_coming_submit",
                "url": "/late-coming/submit",
                "actions": ["view", "add"],
                "default_roles": ["employee", "hr"],
            },
            {
                "key": "emp_sunday_duty",
                "name": "My Sunday Duty",
                "description": "Check allocated Sunday duties and duty details",
                "endpoint": "emp.my_sunday_duty",
                "url": "/my-sunday-duty",
                "actions": ["view"],
                "default_roles": ["employee", "hr"],
            },
            {
                "key": "emp_face_id",
                "name": "Face ID Management",
                "description": "Request re-registration of face biometric or view credentials",
                "endpoint": "emp.face_change",
                "url": "/face-change",
                "actions": ["view", "add"],
                "default_roles": ["employee", "hr"],
            },
        ],
    },
]

# Quick lookup dictionary of all modules by key
ALL_MODULES = {}
for _grp in MODULE_GROUPS:
    for _m in _grp["modules"]:
        ALL_MODULES[_m["key"]] = _m

ALL_MODULE_KEYS = set(ALL_MODULES.keys())


def get_admin_code() -> str:
    """Returns the configured root administrator employee code."""
    return (os.environ.get("ADMIN_CODE") or "HR001").strip().upper()


def is_admin_user(user) -> bool:
    """Requirement 8: Admin must always retain full access.
    Returns True if the given user is the system administrator.
    """
    if not user:
        return False
    emp_code = ""
    try:
        emp_code = str(user["emp_code"] or "").strip().upper()
    except (KeyError, IndexError, TypeError):
        try:
            emp_code = str(getattr(user, "emp_code", "") or "").strip().upper()
        except Exception:
            pass
    admin_code = get_admin_code()
    return bool(emp_code and emp_code == admin_code)


def get_full_permissions_dict() -> dict:
    """Returns a permissions dictionary where all modules have all actions granted (1)."""
    perms = {}
    for mod_key, mod_info in ALL_MODULES.items():
        perms[mod_key] = {
            "can_view": 1,
            "can_add": 1 if "add" in mod_info["actions"] else 0,
            "can_edit": 1 if "edit" in mod_info["actions"] else 0,
            "can_delete": 1 if "delete" in mod_info["actions"] else 0,
            "can_export": 1 if "export" in mod_info["actions"] else 0,
        }
    return perms


def get_default_permissions_for_role(role: str) -> dict:
    """Computes default permissions based on role (hr vs employee).
    Maintains 100% backward compatibility for existing users without custom overrides.
    """
    role = (role or "employee").lower()
    perms = {}
    for mod_key, mod_info in ALL_MODULES.items():
        has_role = role in mod_info.get("default_roles", [])
        perms[mod_key] = {
            "can_view": 1 if has_role and "view" in mod_info["actions"] else 0,
            "can_add": 1 if has_role and "add" in mod_info["actions"] else 0,
            "can_edit": 1 if has_role and "edit" in mod_info["actions"] else 0,
            "can_delete": 1 if has_role and "delete" in mod_info["actions"] else 0,
            "can_export": 1 if has_role and "export" in mod_info["actions"] else 0,
        }
    return perms


def get_user_permissions(db, user_id: int, user_role: str = None, emp_code: str = None):
    """Loads effective permissions for a user.
    Returns (permissions_dict, is_custom_boolean).
    If user is Admin (HR001), returns full access unconditionally.
    If custom records exist in user_page_permissions, returns the custom permissions.
    Otherwise, falls back to default role permissions.
    """
    admin_code = get_admin_code()
    if emp_code and emp_code.strip().upper() == admin_code:
        return get_full_permissions_dict(), False

    # Check if user has custom records in user_page_permissions
    rows = db.execute(
        "SELECT module_key, can_view, can_add, can_edit, can_delete, can_export "
        "FROM user_page_permissions WHERE user_id = ?",
        (user_id,)
    ).fetchall()

    if rows:
        perms = {}
        for r in rows:
            mod_key = r["module_key"]
            if mod_key in ALL_MODULES:
                perms[mod_key] = {
                    "can_view": int(r["can_view"]),
                    "can_add": int(r["can_add"]),
                    "can_edit": int(r["can_edit"]),
                    "can_delete": int(r["can_delete"]),
                    "can_export": int(r["can_export"]),
                }
        # Ensure any missing modules are initialized with 0
        for mod_key in ALL_MODULE_KEYS:
            if mod_key not in perms:
                perms[mod_key] = {"can_view": 0, "can_add": 0, "can_edit": 0, "can_delete": 0, "can_export": 0}
        return perms, True

    # No custom permissions configured: use role defaults
    return get_default_permissions_for_role(user_role), False


def has_permission(user, module_key: str, action: str = "view") -> bool:
    """Checks whether the given user has permission to perform `action` on `module_key`.
    - Admin (HR001) ALWAYS returns True.
    - Uses user's loaded permissions dictionary or loads dynamically.
    - Action must be one of: view, add, edit, delete, export.
    """
    if not user:
        return False

    # Requirement 8: Admin must always retain full access
    if is_admin_user(user):
        return True

    perms = user.get("permissions")
    if perms is None:
        try:
            from .db import get_db
            perms, _ = get_user_permissions(
                get_db(),
                user["id"],
                user.get("role"),
                user.get("emp_code")
            )
        except Exception:
            perms = get_default_permissions_for_role(user.get("role"))

    mod_perm = perms.get(module_key)
    if not mod_perm:
        return False

    field = f"can_{action.lower()}"
    return bool(mod_perm.get(field, 0))


def permission_required(module_key: str, action: str = "view"):
    """Route decorator enforcing granular role/page permission.
    If unauthorized:
      - API / JSON requests: returns 403 Forbidden with JSON error response.
      - Standard Web requests: aborts with 403 Forbidden error page.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not g.user:
                return redirect(url_for("auth.login", next=request.path))

            if g.user.get("must_change_pw") and request.endpoint not in ("auth.change_password", "auth.logout"):
                flash("Please set a new password to continue.", "warn")
                return redirect(url_for("auth.change_password"))

            if not has_permission(g.user, module_key, action):
                mod_name = ALL_MODULES.get(module_key, {}).get("name", module_key)
                err_msg = f"Access Denied: You do not have '{action.capitalize()}' permission for {mod_name}."
                if request.path.startswith("/api/") or request.is_json:
                    return jsonify(ok=False, error=err_msg, code=403), 403
                abort(403, description=err_msg)

            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def save_user_permissions(db, target_user_id: int, permissions_dict: dict, updated_by_id: int, ip_address: str = None) -> dict:
    """Saves granular permissions for target_user_id in user_page_permissions table.
    Enforces Admin safeguards, logs to permission_audit_log and audit_log, and clears caches.
    """
    # Fetch target user info
    target_user = db.execute("SELECT id, emp_code, name, role FROM employees WHERE id = ?", (target_user_id,)).fetchone()
    if not target_user:
        raise ValueError(f"User with ID {target_user_id} not found.")

    admin_code = get_admin_code()
    if target_user["emp_code"] == admin_code:
        # Admin always retains full access
        return {
            "success": True,
            "message": f"Administrator ({target_user['name']}) always retains full access.",
            "is_admin": True
        }

    # Fetch previous permissions to record diff in audit log
    prev_perms, _ = get_user_permissions(db, target_user_id, target_user["role"], target_user["emp_code"])

    # Prepare changes summary
    changes = []
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Delete existing custom records for this user
    db.execute("DELETE FROM user_page_permissions WHERE user_id = ?", (target_user_id,))

    # Insert updated permissions
    for mod_key, mod_info in ALL_MODULES.items():
        user_mod_data = permissions_dict.get(mod_key, {})
        can_v = 1 if int(user_mod_data.get("can_view", 0)) == 1 and "view" in mod_info["actions"] else 0
        can_a = 1 if int(user_mod_data.get("can_add", 0)) == 1 and "add" in mod_info["actions"] else 0
        can_e = 1 if int(user_mod_data.get("can_edit", 0)) == 1 and "edit" in mod_info["actions"] else 0
        can_d = 1 if int(user_mod_data.get("can_delete", 0)) == 1 and "delete" in mod_info["actions"] else 0
        can_x = 1 if int(user_mod_data.get("can_export", 0)) == 1 and "export" in mod_info["actions"] else 0

        db.execute(
            "INSERT INTO user_page_permissions "
            "(user_id, module_key, can_view, can_add, can_edit, can_delete, can_export, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (target_user_id, mod_key, can_v, can_a, can_e, can_d, can_x, now_ts, updated_by_id)
        )

        prev_mod = prev_perms.get(mod_key, {})
        diffs = []
        for act, val in [("view", can_v), ("add", can_a), ("edit", can_e), ("delete", can_d), ("export", can_x)]:
            prev_val = prev_mod.get(f"can_{act}", 0)
            if val != prev_val:
                diffs.append(f"{act}:{prev_val}->{val}")
        if diffs:
            changes.append(f"{mod_key}({', '.join(diffs)})")

    # Commit transaction
    db.commit()

    # Resolve client IP safely with or without request context
    client_ip = ip_address
    if not client_ip:
        if has_request_context():
            try:
                client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            except Exception:
                client_ip = "127.0.0.1"
        else:
            client_ip = "127.0.0.1"

    # Log to audit trail
    details_str = f"Updated permissions for {target_user['name']} ({target_user['emp_code']}). Changes: {'; '.join(changes) if changes else 'No changes (re-saved)'}"
    try:
        db.execute(
            "INSERT INTO permission_audit_log (target_user_id, changed_by_id, action, details, ip_address, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (target_user_id, updated_by_id, "UPDATE_PERMISSIONS", details_str, client_ip, now_ts)
        )
        db.commit()
    except Exception as e:
        logger.warning("Could not write to permission_audit_log: %s", e)

    try:
        from .services import audit
        actor = getattr(g, "user", None) or {"id": updated_by_id, "name": "Admin"}
        audit("PERMISSION_UPDATE", entity="employees", entity_id=str(target_user_id), details=details_str, actor=actor)
    except Exception as e:
        logger.warning("Could not write to main audit_log: %s", e)

    # Invalidate session caches so changes take effect immediately
    try:
        from .auth import clear_user_cache
        clear_user_cache(target_user_id)
    except Exception:
        pass

    try:
        from .turso_db import clear_turso_cache
        clear_turso_cache()
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Permissions updated successfully for {target_user['name']} ({target_user['emp_code']}).",
        "changes_count": len(changes)
    }


def reset_user_permissions(db, target_user_id: int, updated_by_id: int, ip_address: str = None) -> dict:
    """Resets user permissions back to default for their role by clearing custom records."""
    target_user = db.execute("SELECT id, emp_code, name, role FROM employees WHERE id = ?", (target_user_id,)).fetchone()
    if not target_user:
        raise ValueError(f"User with ID {target_user_id} not found.")

    db.execute("DELETE FROM user_page_permissions WHERE user_id = ?", (target_user_id,))
    db.commit()

    # Resolve client IP safely with or without request context
    client_ip = ip_address
    if not client_ip:
        if has_request_context():
            try:
                client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            except Exception:
                client_ip = "127.0.0.1"
        else:
            client_ip = "127.0.0.1"

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    details_str = f"Reset permissions to {target_user['role'].upper()} default for {target_user['name']} ({target_user['emp_code']})."

    try:
        db.execute(
            "INSERT INTO permission_audit_log (target_user_id, changed_by_id, action, details, ip_address, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (target_user_id, updated_by_id, "RESET_TO_DEFAULT", details_str, client_ip, now_ts)
        )
        db.commit()
    except Exception as e:
        logger.warning("Could not write to permission_audit_log: %s", e)

    try:
        from .services import audit
        actor = getattr(g, "user", None) or {"id": updated_by_id, "name": "Admin"}
        audit("PERMISSION_RESET", entity="employees", entity_id=str(target_user_id), details=details_str, actor=actor)
    except Exception as e:
        logger.warning("Could not write to main audit_log: %s", e)

    try:
        from .auth import clear_user_cache
        clear_user_cache(target_user_id)
    except Exception:
        pass

    try:
        from .turso_db import clear_turso_cache
        clear_turso_cache()
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Permissions reset to default {target_user['role'].upper()} role for {target_user['name']}."
    }
