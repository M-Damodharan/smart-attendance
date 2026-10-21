"""SBG Cyber Defense & AI Security Shield.
Protects the attendance application, database, authentication, and AI assistant
from attacks, data tampering, prompt injections, SQLi, XSS, and unauthorized data leaks.
"""
import os
from datetime import datetime
import html
import re
from flask import abort, current_app, g, has_request_context, jsonify, request, session
from .db import get_db
from .services import d2s, now, today, DT_FMT


# -------------------------------------------------------------------------
# Attack Signatures & Threat Classifications
# -------------------------------------------------------------------------

# 1. Prompt Injection & AI Jailbreak Signatures
PROMPT_INJECTION_PATTERNS = [
    r"\bignore\s+(all\s+)?(previous|prior|above)\s+instructions\b",
    r"\breveal\s+(your\s+)?(system\s+prompt|instructions|secret|developer\s+mode)\b",
    r"\b(jailbreak|dan\s+mode|unrestricted\s+mode|bypass\s+security|bypass\s+filter)\b",
    r"\bpretend\s+(you\s+are|to\s+be)\s+(an\s+admin|root|unrestricted|hacker)\b",
    r"\byou\s+are\s+now\s+in\s+(developer|unrestricted|god)\s+mode\b",
    r"\b(show|get|list|display)\s+(me\s+)?(all\s+)?(passwords?|password_hash(es)?|salts?|secret_keys?|hash(es)?)\b",
    r"\b(dump|leak|steal|extract|hack)\s+(the\s+)?(database|users|employees|attendance|audit_log|sqlite|data)\b",
    r"\bexport\s+all\s+(passwords?|credentials|hashes|tokens)\b",
    r"\bprint\s+system\s+(prompt|config|environment|variables)\b",
]

# 2. SQL Injection Signatures
SQLI_PATTERNS = [
    r"(\bUNION\s+SELECT\b|\bSELECT\s+\*\s+FROM\b|\bDROP\s+TABLE\b|\bALTER\s+TABLE\b)",
    r"(\bINSERT\s+INTO\b|\bUPDATE\s+employees\s+SET\b|\bDELETE\s+FROM\b)",
    r"('.+--|'.+\/\*|;\s*--|;\s*DROP\b|;\s*DELETE\b)",
    r"(\bOR\s+['\"]?1['\"]?\s*=\s*['\"]?1|\bAND\s+['\"]?1['\"]?\s*=\s*['\"]?1)",
    r"(\bSLEEP\s*\(|\bBENCHMARK\s*\(|\bWAITFOR\s+DELAY\b)",
    r"(\bsqlite_master\b|\binformation_schema\b)",
]

# 3. Cross-Site Scripting (XSS) & Code Injection Signatures
XSS_PATTERNS = [
    r"<\s*script\b[^>]*>",
    r"javascript\s*:",
    r"data\s*:\s*text\/html",
    r"<\s*img\b[^>]+onerror\s*=",
    r"<\s*svg\b[^>]+onload\s*=",
    r"<\s*iframe\b",
    r"document\s*\.\s*(cookie|location|write)",
]

# 4. Path Traversal & OS Command Injection Signatures
PATH_TRAVERSAL_PATTERNS = [
    r"(\.\.\/|\.\.\\|%2e%2e%2f|%2e%2e\/|\.\.%2f)",
    r"(\/etc\/passwd|\/etc\/shadow|c:\\windows\\system32)",
    r"(;\s*(ls|cat|whoami|id|bash|sh|cmd|powershell)\b)",
]


# Compile regex patterns for high-speed evaluation
REGEX_PROMPT_INJECTION = [re.compile(p, re.IGNORECASE) for p in PROMPT_INJECTION_PATTERNS]
REGEX_SQLI = [re.compile(p, re.IGNORECASE) for p in SQLI_PATTERNS]
REGEX_XSS = [re.compile(p, re.IGNORECASE) for p in XSS_PATTERNS]
REGEX_PATH = [re.compile(p, re.IGNORECASE) for p in PATH_TRAVERSAL_PATTERNS]


def scan_threats(text: str, check_prompt_injection: bool = True):
    """Scans input text for cyber threats, prompt injections, and exploit attempts.
    Returns: (is_threat: bool, threat_type: str, matched_pattern: str)
    """
    if not text or not isinstance(text, str):
        return False, "", ""

    # Check for Prompt Injection / AI Jailbreaking
    if check_prompt_injection:
        for rx in REGEX_PROMPT_INJECTION:
            m = rx.search(text)
            if m:
                return True, "PROMPT_INJECTION_ATTEMPT", m.group(0)

    # Check for SQL Injection
    for rx in REGEX_SQLI:
        m = rx.search(text)
        if m:
            return True, "SQL_INJECTION_ATTEMPT", m.group(0)

    # Check for XSS
    for rx in REGEX_XSS:
        m = rx.search(text)
        if m:
            return True, "XSS_ATTACK_ATTEMPT", m.group(0)

    # Check for Path Traversal
    for rx in REGEX_PATH:
        m = rx.search(text)
        if m:
            return True, "PATH_TRAVERSAL_ATTEMPT", m.group(0)

    return False, "", ""


def sanitize_input(text: str) -> str:
    """Sanitizes user input by stripping dangerous tags and escaping HTML entities."""
    if not text or not isinstance(text, str):
        return ""
    # Strip null bytes and control chars
    clean = text.replace("\0", "").strip()
    return html.escape(clean, quote=True)


def log_security_event(threat_type: str, payload: str, source: str = "WAF", actor_id: int = None, actor_name: str = None):
    """Persists security breach attempts to the audit log."""
    try:
        db = get_db()
        if has_request_context():
            actor_id = actor_id or session.get("uid")
            actor_name = actor_name or session.get("name") or "Anonymous/Attacker"
            ip = request.headers.get("X-Forwarded-For", request.remote_addr) if request else "0.0.0.0"
        else:
            actor_id = actor_id
            actor_name = actor_name or "Anonymous/Attacker"
            ip = "127.0.0.1"

        clean_payload = (payload or "")[:250].replace("\n", " ")
        details = f"[{source}] Threat Type: {threat_type} | Snippet: {clean_payload}"
        now_ts = now().strftime(DT_FMT)

        db.execute(
            "INSERT INTO audit_log (ts, actor_id, actor_name, action, entity, entity_id, details, ip) "
            "VALUES (?, ?, ?, 'CYBER_ATTACK_BLOCKED', 'security_shield', ?, ?, ?)",
            (now_ts, actor_id, actor_name, threat_type, details, ip),
        )
        db.commit()
    except Exception:
        pass


def waf_request_interceptor():
    """Web Application Firewall (WAF) middleware registered before every request."""
    # Skip static files
    path = request.path
    if path.startswith("/static/") or path == "/favicon.ico":
        return

    is_ai_endpoint = path.startswith("/api/ai") or path.startswith("/ai-assistant")

    # 1. Inspect URL parameters
    for k, v in request.args.items():
        is_threat, threat_type, matched = scan_threats(f"{k} {v}", check_prompt_injection=is_ai_endpoint)
        if is_threat:
            log_security_event(threat_type, f"GET query param: {k}={matched}", source="WAF_URL")
            abort(403, "Access Denied: Malicious activity detected by SBG Cyber Defense Shield.")

    # 2. Inspect Form submissions
    if request.form:
        for k, v in request.form.items():
            if k in ("password", "old_password", "new_password", "confirm_password", "csrf_token"):
                continue  # Passwords and CSRF tokens may contain arbitrary chars
            # Skip large base64 strings or image payloads
            if isinstance(v, str) and (len(v) > 4000 or v.startswith("data:image/")):
                continue
            is_threat, threat_type, matched = scan_threats(str(v), check_prompt_injection=is_ai_endpoint)
            if is_threat:
                log_security_event(threat_type, f"POST form field: {k}={matched}", source="WAF_FORM")
                abort(403, "Access Denied: Malicious request payload blocked by SBG Cyber Defense Shield.")

    # 3. Inspect JSON payloads
    if request.is_json:
        data = request.get_json(silent=True) or {}
        if isinstance(data, dict):
            for k, v in data.items():
                # Skip image/biometric data, frames, and CSRF tokens from WAF regex inspection
                if k in ("selfie", "frames", "in_selfie", "out_selfie", "image", "photo", "csrf_token", "csrf"):
                    continue
                if isinstance(v, str):
                    if len(v) > 4000 or v.startswith("data:image/"):
                        continue
                    is_threat, threat_type, matched = scan_threats(v, check_prompt_injection=is_ai_endpoint)
                    if is_threat:
                        log_security_event(threat_type, f"JSON payload: {k}={matched}", source="WAF_JSON")
                        resp = jsonify({
                            "ok": False,
                            "error": "Access Denied: Malicious payload blocked by SBG Cyber Defense Shield.",
                            "shield_status": "BLOCKED"
                        })
                        resp.status_code = 403
                        return resp


def redact_sensitive_data(data: dict) -> dict:
    """Recursively removes sensitive hashes, private keys, and passwords from dictionary."""
    if not isinstance(data, dict):
        return data
    SENSITIVE_KEYS = {"password", "password_hash", "salt", "secret", "secret_key", "csrf"}
    sanitized = {}
    for k, v in data.items():
        if k.lower() in SENSITIVE_KEYS:
            sanitized[k] = "[REDACTED_SECURITY_POLICY]"
        elif isinstance(v, dict):
            sanitized[k] = redact_sensitive_data(v)
        elif isinstance(v, list):
            sanitized[k] = [redact_sensitive_data(i) if isinstance(i, dict) else i for i in v]
        else:
            sanitized[k] = v
    return sanitized


# -------------------------------------------------------------------------
# AI Biometric Buddy Punch & Real-Time Face Defense Engine
# -------------------------------------------------------------------------

def block_employee_account_by_ai(db, emp_id: int, reason: str, target_emp_id: int = None,
                                 similarity: float = 0.0, selfie_path: str = None, target_selfie_path: str = None):
    """Immediately locks and blocks an employee ID upon detecting buddy punching,
    duplicate face spoofing, or cross-account biometric violations.
    Requires HR permission to unlock.
    """
    now_ts = now().strftime(DT_FMT)
    date_str = d2s(today())

    # 1. Block employee account
    db.execute(
        "UPDATE employees SET active=0, block_reason=?, blocked_at=? WHERE id=?",
        (reason, now_ts, emp_id)
    )

    # 2. Record incident in ai_face_alerts (avoid duplicate open alerts for same emp/date)
    existing = db.execute(
        "SELECT id FROM ai_face_alerts WHERE date=? AND emp_id=? AND (target_emp_id=? OR target_emp_id IS NULL) AND status='BLOCKED'",
        (date_str, emp_id, target_emp_id)
    ).fetchone()

    if not existing:
        db.execute(
            "INSERT INTO ai_face_alerts (date, emp_id, target_emp_id, similarity, selfie_path, "
            "target_selfie_path, alert_type, status, details, created_at) VALUES (?,?,?,?,?,?, 'BUDDY_PUNCH', 'BLOCKED', ?, ?)",
            (date_str, emp_id, target_emp_id, round(float(similarity), 4), selfie_path, target_selfie_path, reason, now_ts)
        )
    else:
        db.execute(
            "UPDATE ai_face_alerts SET similarity=MAX(similarity, ?), selfie_path=COALESCE(?, selfie_path), "
            "target_selfie_path=COALESCE(?, target_selfie_path), details=?, created_at=? WHERE id=?",
            (round(float(similarity), 4), selfie_path, target_selfie_path, reason, now_ts, existing["id"])
        )

    # 3. Audit log
    db.execute(
        "INSERT INTO audit_log (ts, action, entity, entity_id, details) VALUES (?, 'AI_ACCOUNT_BLOCKED', 'employee', ?, ?)",
        (now_ts, str(emp_id), f"Account locked by AI Defense Shield. Reason: {reason}")
    )
    db.commit()


def _resolve_image_path(filename: str):
    """Safely resolves an image path across configured SELFIE_DIR and instance storage."""
    if not filename:
        return None
    if os.path.isabs(filename) and os.path.exists(filename):
        return filename
    safe_name = os.path.basename(filename)
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            s_dir = current_app.config.get("SELFIE_DIR")
            if s_dir:
                p = os.path.join(s_dir, safe_name)
                if os.path.exists(p):
                    return p
            base_dir = current_app.config.get("BASE_DIR") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            p2 = os.path.join(base_dir, "instance", "selfies", safe_name)
            if os.path.exists(p2):
                return p2
    except Exception:
        pass
    fallback = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), "instance", "selfies", safe_name)
    if os.path.exists(fallback):
        return fallback
    return None


def check_cross_punch_buddy_face_realtime(db, live_img, emp_id: int, date_str: str = None, device_credential_id: str = None) -> dict:
    """Real-Time Cross-Punch Buddy Face & Biometric Defense:
    Executed before ANY attendance punch (both WebAuthn and camera-based).
    Compares the incoming live camera selfie against:
    1. Phone hardware Face ID credential used by any other employee today.
    2. All attendance check-in/check-out photos recorded in the database by ANY OTHER employee
       (both today's punches and historical stored photos, up to 400 images across all 4 departments).
    3. All other employees' registered 360-degree face templates and profile photos across all 4 departments.
    If similarity >= threshold (calibrated 0.50 for phone cameras), immediately flags a buddy-punch violation,
    allowing caller to auto-block the employee ID and abort the punch.
    """
    import os
    from flask import current_app
    from .services import d2s, today
    from .face_engine import (
        get_candidate_live_embeddings,
        compute_multi_angle_similarity,
        parse_template_angles,
        extract_face_embedding
    )

    if not date_str:
        date_str = d2s(today())

    # 1. Device Credential / Phone Sharing Check:
    # If the exact same Phone Face ID / WebAuthn passkey was already used by another employee today, block immediately!
    if device_credential_id:
        dev_other = db.execute(
            "SELECT a.emp_id, a.date, e.emp_code, e.name, e.department, c.name as category_name "
            "FROM attendance a "
            "JOIN employees e ON e.id = a.emp_id "
            "LEFT JOIN employee_categories c ON c.id = e.category_id "
            "WHERE a.device_credential_id = ? AND a.emp_id != ? AND a.date = ? "
            "ORDER BY a.id DESC LIMIT 1",
            (device_credential_id, emp_id, date_str)
        ).fetchone()
        if dev_other:
            d_dept = dev_other["department"] or dev_other["category_name"] or "General"
            return {
                "violation": True,
                "type": "DEVICE_SHARING_VIOLATION",
                "similarity": 1.0,
                "other_emp": dict(dev_other),
                "other_selfie": None,
                "message": (
                    f"AI Biometric Device Blocked: This phone's Face ID was already used today by "
                    f"employee {dev_other['name']} ({dev_other['emp_code']} · {d_dept}). "
                    f"Buddy punch / device sharing rejected."
                )
            }

    # 2. Extract multi-scale invariant candidate vectors from incoming live image
    try:
        live_candidates = get_candidate_live_embeddings(live_img)
    except Exception:
        try:
            live_candidates = [extract_face_embedding(live_img)]
        except Exception as e:
            return {"violation": False, "error": f"Failed to extract face features: {e}"}

    # 3. Dynamic similarity threshold calibrated for real smartphone cameras (0.60)
    thresh_row = db.execute("SELECT value FROM system_settings WHERE key='ai_buddy_punch_block_threshold'").fetchone()
    val = float(thresh_row["value"]) if thresh_row and thresh_row["value"] else 0.60
    threshold = min(val, 0.60)

    # 4. Check against ALL punch photos stored across the database for ANY OTHER employee
    # Scans up to 400 attendance images across all 4 departments / categories for other IDs
    att_rows = db.execute(
        "SELECT a.id, a.emp_id, a.date, a.in_selfie, a.out_selfie, a.check_in, a.device_credential_id, "
        "e.emp_code, e.name, e.department, e.face_image, c.name as category_name "
        "FROM attendance a "
        "JOIN employees e ON e.id=a.emp_id "
        "LEFT JOIN employee_categories c ON c.id=e.category_id "
        "WHERE a.emp_id != ? AND (a.in_selfie IS NOT NULL OR a.out_selfie IS NOT NULL) "
        "ORDER BY a.id DESC LIMIT 400",
        (emp_id,)
    ).fetchall()

    for r in att_rows:
        for s_file in (r["in_selfie"], r["out_selfie"]):
            if not s_file:
                continue
            s_path = _resolve_image_path(s_file)
            if not s_path:
                continue
            try:
                other_candidates = get_candidate_live_embeddings(s_path)
                sim = compute_multi_angle_similarity(live_candidates, other_candidates)
                if sim >= threshold:
                    is_today = (r["date"] == date_str)
                    v_type = "TODAY_PUNCH_DUPLICATE" if is_today else "STORED_PUNCH_DUPLICATE"
                    date_label = "today" if is_today else f"on {r['date']}"
                    dept_str = r["department"] or r["category_name"] or "General"
                    return {
                        "violation": True,
                        "type": v_type,
                        "similarity": sim,
                        "other_emp": dict(r),
                        "other_selfie": s_file,
                        "message": (
                            f"AI Buddy Punch Blocked [4-Department 400-Image Defense]: Live face matches employee {r['name']} ({r['emp_code']} · {dept_str}) "
                            f"attendance photo from {date_label} (Similarity: {sim*100:.1f}%)."
                        )
                    }
            except Exception:
                pass

    # 5. Check against all OTHER registered employee templates & profile images across all 4 departments
    reg_rows = db.execute(
        "SELECT e.id, e.emp_code, e.name, e.department, e.face_template, e.face_image, c.name as category_name "
        "FROM employees e "
        "LEFT JOIN employee_categories c ON c.id=e.category_id "
        "WHERE e.id != ? AND (e.face_registered=1 OR e.face_image IS NOT NULL OR e.face_template IS NOT NULL)",
        (emp_id,)
    ).fetchall()

    for r in reg_rows:
        sim = 0.0
        # Check template angles if available
        if r["face_template"]:
            stored_angles = parse_template_angles(r["face_template"])
            if stored_angles:
                sim = max(sim, compute_multi_angle_similarity(live_candidates, stored_angles))
        # Also check registered face_image file
        if r["face_image"]:
            reg_img_path = _resolve_image_path(r["face_image"])
            if reg_img_path:
                try:
                    reg_candidates = get_candidate_live_embeddings(reg_img_path)
                    sim = max(sim, compute_multi_angle_similarity(live_candidates, reg_candidates))
                except Exception:
                    pass

        if sim >= threshold:
            dept_str = r["department"] or r["category_name"] or "General"
            return {
                "violation": True,
                "type": "REGISTERED_FACE_MATCH",
                "similarity": sim,
                "other_emp": dict(r),
                "other_selfie": r["face_image"],
                "message": (
                    f"AI Biometric Policy Violation [4-Department 400-Image Defense]: Live face matches registered face of employee {r['name']} ({r['emp_code']} · {dept_str}) "
                    f"(Similarity: {sim*100:.1f}%)."
                )
            }

    return {"violation": False}


def unlock_employee_account_by_hr(db, alert_id: int, hr_id: int, notes: str = ""):
    """Allows HR Administrator to review incident, grant permission, and unlock employee account."""
    alert = db.execute("SELECT * FROM ai_face_alerts WHERE id=?", (alert_id,)).fetchone()
    if not alert:
        return False

    now_ts = now().strftime(DT_FMT)

    # 1. Unlock employee
    db.execute(
        "UPDATE employees SET active=1, block_reason=NULL, blocked_at=NULL WHERE id=?",
        (alert["emp_id"],)
    )

    # 2. Update alert status
    db.execute(
        "UPDATE ai_face_alerts SET status='RESOLVED_UNLOCKED', hr_action_by=?, hr_action_at=?, hr_notes=? WHERE id=?",
        (hr_id, now_ts, notes or "Permission Granted & Unlocked by HR", alert_id)
    )

    # 3. Log audit
    db.execute(
        "INSERT INTO audit_log (ts, action, entity, entity_id, details) VALUES (?, 'AI_ACCOUNT_UNLOCKED', 'employee', ?, ?)",
        (now_ts, str(alert["emp_id"]), f"Unlocked by HR ID {hr_id}. Notes: {notes}")
    )
    db.commit()
    return True


def scan_daily_punches_for_buddy_punches(db, target_date_str: str = None, threshold: float = None) -> list:
    """Automated AI Database Scanner:
    Inspects all daily attendance punch photos in the database.
    Detects if the same face was used across multiple employee IDs (EMP001 vs EMP002 buddy punch),
    comparing each punch against today's punches, all stored historical punches, and registered templates.
    Flags violations and blocks offending accounts pending HR permission.
    """
    import os
    import re
    from flask import current_app
    from .services import d2s, today
    from .face_engine import (
        get_candidate_live_embeddings,
        compute_multi_angle_similarity,
        parse_template_angles,
        extract_face_embedding
    )

    if target_date_str:
        s = str(target_date_str).strip()
        m = re.match(r"^(\d{2})[-/](\d{2})[-/](\d{4})$", s)
        if m:
            target_date_str = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    else:
        target_date_str = d2s(today())

    if threshold is None:
        thresh_row = db.execute("SELECT value FROM system_settings WHERE key='ai_buddy_punch_block_threshold'").fetchone()
        val = float(thresh_row["value"]) if thresh_row and thresh_row["value"] else 0.60
        threshold = min(val, 0.60)

    # 1. Fetch all punches for the target date across ALL departments
    rows = db.execute(
        "SELECT a.id, a.emp_id, a.date, a.in_selfie, a.out_selfie, a.check_in, "
        "e.emp_code, e.name, e.department, e.face_image, e.face_template, c.name as category_name "
        "FROM attendance a "
        "JOIN employees e ON e.id=a.emp_id "
        "LEFT JOIN employee_categories c ON c.id=e.category_id "
        "WHERE a.date=? AND (a.in_selfie IS NOT NULL OR a.out_selfie IS NOT NULL)",
        (target_date_str,)
    ).fetchall()

    detected = []
    punch_items = []

    for r in rows:
        for s_name in (r["in_selfie"], r["out_selfie"]):
            if not s_name:
                continue
            s_path = _resolve_image_path(s_name)
            if s_path:
                try:
                    candidates = get_candidate_live_embeddings(s_path)
                    punch_items.append({
                        "rec_id": r["id"],
                        "emp_id": r["emp_id"],
                        "emp_code": r["emp_code"],
                        "name": r["name"],
                        "department": r["department"] or r["category_name"] or "General",
                        "category_name": r["category_name"] or "",
                        "selfie_file": s_name,
                        "candidates": candidates,
                    })
                    break
                except Exception:
                    try:
                        candidates = [extract_face_embedding(s_path)]
                        punch_items.append({
                            "rec_id": r["id"],
                            "emp_id": r["emp_id"],
                            "emp_code": r["emp_code"],
                            "name": r["name"],
                            "department": r["department"] or r["category_name"] or "General",
                            "category_name": r["category_name"] or "",
                            "selfie_file": s_name,
                            "candidates": candidates,
                        })
                        break
                    except Exception:
                        pass

    # 2. Fetch all other stored historical punches from attendance across ALL 4 departments (up to 400 images)
    hist_rows = db.execute(
        "SELECT a.id, a.emp_id, a.date, a.in_selfie, a.out_selfie, "
        "e.emp_code, e.name, e.department, e.face_image, c.name as category_name "
        "FROM attendance a "
        "JOIN employees e ON e.id=a.emp_id "
        "LEFT JOIN employee_categories c ON c.id=e.category_id "
        "WHERE a.date != ? AND (a.in_selfie IS NOT NULL OR a.out_selfie IS NOT NULL) "
        "ORDER BY a.id DESC LIMIT 400",
        (target_date_str,)
    ).fetchall()

    hist_items = []
    for hr in hist_rows:
        s_name = hr["in_selfie"] or hr["out_selfie"]
        if s_name:
            sp = _resolve_image_path(s_name)
            if sp:
                try:
                    cands = get_candidate_live_embeddings(sp)
                    hist_items.append({
                        "emp_id": hr["emp_id"],
                        "emp_code": hr["emp_code"],
                        "name": hr["name"],
                        "department": hr["department"] or hr["category_name"] or "General",
                        "category_name": hr["category_name"] or "",
                        "date": hr["date"],
                        "selfie_file": s_name,
                        "candidates": cands
                    })
                except Exception:
                    pass

    # 3. Fetch all other registered employee templates and photos across ALL 4 departments
    all_emps = db.execute(
        "SELECT e.id, e.emp_code, e.name, e.department, e.face_image, e.face_template, c.name as category_name "
        "FROM employees e "
        "LEFT JOIN employee_categories c ON c.id=e.category_id "
        "WHERE (e.face_registered=1 OR e.face_image IS NOT NULL OR e.face_template IS NOT NULL)"
    ).fetchall()

    # Evaluation A: Cross-Punch Evaluation within target_date (Same face on 2 IDs today)
    n = len(punch_items)
    for i in range(n):
        for j in range(i + 1, n):
            p1 = punch_items[i]
            p2 = punch_items[j]
            if p1["emp_id"] == p2["emp_id"]:
                continue

            sim = compute_multi_angle_similarity(p1["candidates"], p2["candidates"])
            if sim >= threshold:
                d1 = p1.get("department") or p1.get("category_name") or "General"
                d2 = p2.get("department") or p2.get("category_name") or "General"
                is_same = bool(d1.strip().lower() == d2.strip().lower())
                dept_tag = f"Same Department: {d1}" if is_same else f"4-Department Scan: {d1} ↔ {d2}"
                reason = (
                    f"AI Daily Scan [400-Image 4-Department Audit · {dept_tag}]: Same face punch detected between {p1['emp_code']} ({p1['name']} · {d1}) "
                    f"and {p2['emp_code']} ({p2['name']} · {d2}) on {target_date_str} (Similarity: {sim*100:.1f}%). "
                    f"Account locked pending HR permission."
                )
                block_employee_account_by_ai(
                    db, p2["emp_id"], reason=reason, target_emp_id=p1["emp_id"],
                    similarity=sim, selfie_path=p2["selfie_file"], target_selfie_path=p1["selfie_file"]
                )
                detected.append({
                    "emp1": p1,
                    "emp2": p2,
                    "similarity": sim,
                    "reason": reason
                })

    # Evaluation B: Target date punches vs ALL historical stored punches across ALL 4 departments (up to 400 images)
    for p in punch_items:
        for hp in hist_items:
            if hp["emp_id"] == p["emp_id"]:
                continue
            sim = compute_multi_angle_similarity(p["candidates"], hp["candidates"])
            if sim >= threshold:
                d1 = p.get("department") or p.get("category_name") or "General"
                d2 = hp.get("department") or hp.get("category_name") or "General"
                is_same = bool(d1.strip().lower() == d2.strip().lower())
                dept_tag = f"Same Department: {d1}" if is_same else f"4-Department Scan: {d1} ↔ {d2}"
                reason = (
                    f"AI Daily Scan [400-Image 4-Department Audit · {dept_tag}]: Punch face for {p['emp_code']} ({p['name']} · {d1}) matches stored punch "
                    f"of employee {hp['emp_code']} ({hp['name']} · {d2}) from {hp['date']} (Similarity: {sim*100:.1f}%). "
                    f"Buddy punch blocked."
                )
                block_employee_account_by_ai(
                    db, p["emp_id"], reason=reason, target_emp_id=hp["emp_id"],
                    similarity=sim, selfie_path=p["selfie_file"], target_selfie_path=hp["selfie_file"]
                )
                detected.append({
                    "emp1": p,
                    "emp2": hp,
                    "similarity": sim,
                    "reason": reason
                })

    # Evaluation C: Target date punches vs registered employee templates / face images across ALL 4 departments
    for p in punch_items:
        for other in all_emps:
            if other["id"] == p["emp_id"]:
                continue
            sim = 0.0
            if other["face_template"]:
                other_angles = parse_template_angles(other["face_template"])
                if other_angles:
                    sim = max(sim, compute_multi_angle_similarity(p["candidates"], other_angles))
            if other["face_image"]:
                f_path = _resolve_image_path(other["face_image"])
                if f_path:
                    try:
                        other_cands = get_candidate_live_embeddings(f_path)
                        sim = max(sim, compute_multi_angle_similarity(p["candidates"], other_cands))
                    except Exception:
                        pass

            if sim >= threshold:
                d1 = p.get("department") or p.get("category_name") or "General"
                d2 = other.get("department") or other.get("category_name") or "General"
                is_same = bool(d1.strip().lower() == d2.strip().lower())
                dept_tag = f"Same Department: {d1}" if is_same else f"4-Department Scan: {d1} ↔ {d2}"
                reason = (
                    f"AI Daily Scan [400-Image 4-Department Audit · {dept_tag}]: Punch face for {p['emp_code']} ({p['name']} · {d1}) matches registered face of "
                    f"{other['emp_code']} ({other['name']} · {d2}) (Similarity: {sim*100:.1f}%). Buddy punch blocked."
                )
                block_employee_account_by_ai(
                    db, p["emp_id"], reason=reason, target_emp_id=other["id"],
                    similarity=sim, selfie_path=p["selfie_file"], target_selfie_path=other["face_image"]
                )
                detected.append({
                    "emp1": p,
                    "emp2": dict(other),
                    "similarity": sim,
                    "reason": reason
                })

    # Evaluation D: Duplicate WebAuthn Device usage across different IDs
    dev_rows = db.execute(
        "SELECT a.emp_id, a.device_credential_id, e.emp_code, e.name "
        "FROM attendance a JOIN employees e ON e.id=a.emp_id "
        "WHERE a.date=? AND a.device_credential_id IS NOT NULL",
        (target_date_str,)
    ).fetchall()
    seen_devs = {}
    for dr in dev_rows:
        cid = dr["device_credential_id"]
        if cid in seen_devs and seen_devs[cid]["emp_id"] != dr["emp_id"]:
            first = seen_devs[cid]
            reason = (
                f"AI Multi-Account Violation: Same Phone Biometric Face ID credential was used by both "
                f"{first['emp_code']} ({first['name']}) and {dr['emp_code']} ({dr['name']}) on {target_date_str}. "
                f"Buddy punching strictly blocked."
            )
            block_employee_account_by_ai(
                db, dr["emp_id"], reason=reason, target_emp_id=first["emp_id"], similarity=1.0
            )
            detected.append({"emp1": dict(first), "emp2": dict(dr), "similarity": 1.0, "reason": reason})
        else:
            seen_devs[cid] = dr

    db.commit()
    return detected

