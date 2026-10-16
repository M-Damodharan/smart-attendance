"""SBG Cyber Defense & AI Security Shield.
Protects the attendance application, database, authentication, and AI assistant
from attacks, data tampering, prompt injections, SQLi, XSS, and unauthorized data leaks.
"""
from datetime import datetime
import html
import re
from flask import abort, g, has_request_context, jsonify, request, session
from .db import get_db


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

        db.execute(
            "INSERT INTO audit_log (actor_id, actor_name, action, entity, entity_id, details, ip) "
            "VALUES (?, ?, 'CYBER_ATTACK_BLOCKED', 'security_shield', ?, ?, ?)",
            (actor_id, actor_name, threat_type, details, ip),
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
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = datetime.now().strftime("%Y-%m-%d")

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
            "target_selfie_path, alert_type, status, details) VALUES (?,?,?,?,?,?, 'BUDDY_PUNCH', 'BLOCKED', ?)",
            (date_str, emp_id, target_emp_id, round(float(similarity), 4), selfie_path, target_selfie_path, reason)
        )
    else:
        db.execute(
            "UPDATE ai_face_alerts SET similarity=MAX(similarity, ?), selfie_path=COALESCE(?, selfie_path), "
            "target_selfie_path=COALESCE(?, target_selfie_path), details=? WHERE id=?",
            (round(float(similarity), 4), selfie_path, target_selfie_path, reason, existing["id"])
        )

    # 3. Audit log
    db.execute(
        "INSERT INTO audit_log (action, entity, entity_id, details) VALUES ('AI_ACCOUNT_BLOCKED', 'employee', ?, ?)",
        (str(emp_id), f"Account locked by AI Defense Shield. Reason: {reason}")
    )
    db.commit()


def check_cross_punch_buddy_face_realtime(db, live_img, emp_id: int, date_str: str = None) -> dict:
    """Real-Time Cross-Punch Buddy Face & Biometric Defense:
    Executed before ANY attendance punch (both WebAuthn and camera-based).
    Compares the incoming live camera selfie against:
    1. All attendance check-in/check-out photos recorded today by ANY OTHER employee.
    2. All other employees' registered 360-degree face templates.
    If similarity >= threshold (default 0.65), immediately flags a buddy-punch violation,
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

    # 1. Extract multi-scale invariant candidate vectors from incoming live image
    try:
        live_candidates = get_candidate_live_embeddings(live_img)
    except Exception:
        try:
            live_candidates = [extract_face_embedding(live_img)]
        except Exception as e:
            return {"violation": False, "error": f"Failed to extract face features: {e}"}

    # 2. Get dynamic similarity threshold from system settings (default 0.65 for high accuracy)
    thresh_row = db.execute("SELECT value FROM system_settings WHERE key='ai_buddy_punch_block_threshold'").fetchone()
    threshold = float(thresh_row["value"]) if thresh_row and thresh_row["value"] else 0.65

    selfie_dir = current_app.config.get("SELFIE_DIR")

    # 3. Check against today's punches from OTHER employees
    # (Did another employee ID already check in today with this same face?)
    att_rows = db.execute(
        "SELECT a.id, a.emp_id, a.in_selfie, a.out_selfie, a.check_in, a.device_credential_id, "
        "e.emp_code, e.name, e.department, e.face_image "
        "FROM attendance a "
        "JOIN employees e ON e.id=a.emp_id "
        "WHERE a.date=? AND a.emp_id != ? AND (a.in_selfie IS NOT NULL OR a.out_selfie IS NOT NULL)",
        (date_str, emp_id)
    ).fetchall()

    for r in att_rows:
        for s_file in (r["in_selfie"], r["out_selfie"]):
            if not s_file or not selfie_dir:
                continue
            s_path = os.path.join(selfie_dir, s_file)
            if not os.path.exists(s_path):
                continue
            try:
                other_candidates = get_candidate_live_embeddings(s_path)
                sim = compute_multi_angle_similarity(live_candidates, other_candidates)
                if sim >= threshold:
                    return {
                        "violation": True,
                        "type": "TODAY_PUNCH_DUPLICATE",
                        "similarity": sim,
                        "other_emp": dict(r),
                        "other_selfie": s_file,
                        "message": (
                            f"AI Buddy Punch Blocked: Live face matches employee {r['name']} ({r['emp_code']}) "
                            f"who already punched today (Similarity: {sim*100:.1f}%)."
                        )
                    }
            except Exception:
                pass

    # 4. Check against all OTHER registered employee templates
    # If the user has their own face registered, verify if it matches their own template first
    cur_user = db.execute("SELECT id, face_registered, face_template FROM employees WHERE id=?", (emp_id,)).fetchone()
    if cur_user and cur_user["face_registered"] and cur_user["face_template"]:
        own_angles = parse_template_angles(cur_user["face_template"])
        if own_angles:
            own_sim = compute_multi_angle_similarity(live_candidates, own_angles)
            if own_sim >= 0.70:
                # Face matches the user's own registered account, so not a cross-account template spoof
                return {"violation": False}

    # Only check other employees' registered templates with a strict threshold (0.78)
    reg_threshold = max(threshold, 0.78)
    reg_rows = db.execute(
        "SELECT id, emp_code, name, department, face_template, face_image "
        "FROM employees WHERE face_registered=1 AND id != ?",
        (emp_id,)
    ).fetchall()

    for r in reg_rows:
        if not r["face_template"]:
            continue
        stored_angles = parse_template_angles(r["face_template"])
        if not stored_angles:
            continue
        sim = compute_multi_angle_similarity(live_candidates, stored_angles)
        if sim >= reg_threshold:
            return {
                "violation": True,
                "type": "REGISTERED_FACE_MATCH",
                "similarity": sim,
                "other_emp": dict(r),
                "other_selfie": r["face_image"],
                "message": (
                    f"AI Biometric Policy Violation: Live face matches registered face of employee {r['name']} ({r['emp_code']}) "
                    f"(Similarity: {sim*100:.1f}%)."
                )
            }

    return {"violation": False}


def unlock_employee_account_by_hr(db, alert_id: int, hr_id: int, notes: str = ""):
    """Allows HR Administrator to review incident, grant permission, and unlock employee account."""
    alert = db.execute("SELECT * FROM ai_face_alerts WHERE id=?", (alert_id,)).fetchone()
    if not alert:
        return False

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        "INSERT INTO audit_log (action, entity, entity_id, details) VALUES ('AI_ACCOUNT_UNLOCKED', 'employee', ?, ?)",
        (str(alert["emp_id"]), f"Unlocked by HR ID {hr_id}. Notes: {notes}")
    )
    db.commit()
    return True


def scan_daily_punches_for_buddy_punches(db, target_date_str: str = None, threshold: float = None) -> list:
    """Automated AI Database Scanner:
    Inspects all daily attendance punch photos in the database.
    Detects if the same face was used across multiple employee IDs (EMP001 vs EMP002 buddy punch),
    filtering through variations in dress, eyes, distance, and background noise.
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
        # Normalize DD-MM-YYYY or DD/MM/YYYY to YYYY-MM-DD
        m = re.match(r"^(\d{2})[-/](\d{2})[-/](\d{4})$", s)
        if m:
            target_date_str = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    else:
        target_date_str = d2s(today())

    if threshold is None:
        thresh_row = db.execute("SELECT value FROM system_settings WHERE key='ai_buddy_punch_block_threshold'").fetchone()
        threshold = float(thresh_row["value"]) if thresh_row and thresh_row["value"] else 0.65

    selfie_dir = current_app.config.get("SELFIE_DIR")

    rows = db.execute(
        "SELECT a.id, a.emp_id, a.date, a.in_selfie, a.out_selfie, a.check_in, "
        "e.emp_code, e.name, e.face_image, e.face_template "
        "FROM attendance a "
        "JOIN employees e ON e.id=a.emp_id "
        "WHERE a.date=? AND (a.in_selfie IS NOT NULL OR a.out_selfie IS NOT NULL)",
        (target_date_str,)
    ).fetchall()

    all_emps = db.execute("SELECT id, emp_code, name, face_image, face_template FROM employees WHERE face_registered=1").fetchall()

    detected = []
    punch_items = []

    for r in rows:
        s_name = r["in_selfie"] or r["out_selfie"]
        if not s_name or not selfie_dir:
            continue
        s_path = os.path.join(selfie_dir, s_name)
        if os.path.exists(s_path):
            try:
                candidates = get_candidate_live_embeddings(s_path)
                punch_items.append({
                    "rec_id": r["id"],
                    "emp_id": r["emp_id"],
                    "emp_code": r["emp_code"],
                    "name": r["name"],
                    "selfie_file": s_name,
                    "candidates": candidates,
                })
            except Exception:
                try:
                    candidates = [extract_face_embedding(s_path)]
                    punch_items.append({
                        "rec_id": r["id"],
                        "emp_id": r["emp_id"],
                        "emp_code": r["emp_code"],
                        "name": r["name"],
                        "selfie_file": s_name,
                        "candidates": candidates,
                    })
                except Exception:
                    pass

    # 1. Cross-Punch Evaluation (Did EMP001, CL001, E0012 use the exact same face today?)
    n = len(punch_items)
    for i in range(n):
        for j in range(i + 1, n):
            p1 = punch_items[i]
            p2 = punch_items[j]
            if p1["emp_id"] == p2["emp_id"]:
                continue

            sim = compute_multi_angle_similarity(p1["candidates"], p2["candidates"])
            if sim >= threshold:
                existing = db.execute(
                    "SELECT id FROM ai_face_alerts WHERE date=? AND emp_id=? AND target_emp_id=?",
                    (target_date_str, p2["emp_id"], p1["emp_id"])
                ).fetchone()
                if not existing:
                    reason = (
                        f"AI Daily Scan: Same face punch detected between {p1['emp_code']} ({p1['name']}) "
                        f"and {p2['emp_code']} ({p2['name']}) on {target_date_str} (Similarity: {sim*100:.1f}%). "
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

    # 2. Compare today's punch selfies against all registered templates
    for p in punch_items:
        for other in all_emps:
            if other["id"] == p["emp_id"] or not other["face_template"]:
                continue
            other_angles = parse_template_angles(other["face_template"])
            if not other_angles:
                continue
            sim = compute_multi_angle_similarity(p["candidates"], other_angles)
            if sim >= threshold:
                existing = db.execute(
                    "SELECT id FROM ai_face_alerts WHERE date=? AND emp_id=? AND target_emp_id=?",
                    (target_date_str, p["emp_id"], other["id"])
                ).fetchone()
                if not existing:
                    reason = (
                        f"AI Daily Scan: Punch face for {p['emp_code']} ({p['name']}) matches registered face of "
                        f"{other['emp_code']} ({other['name']}) (Similarity: {sim*100:.1f}%). Buddy punch blocked."
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

    # 3. Check for Duplicate WebAuthn Device usage across different employee IDs today
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

