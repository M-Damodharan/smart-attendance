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


def scan_threats(text: str):
    """Scans input text for cyber threats, prompt injections, and exploit attempts.
    Returns: (is_threat: bool, threat_type: str, matched_pattern: str)
    """
    if not text or not isinstance(text, str):
        return False, "", ""

    # Check for Prompt Injection / AI Jailbreaking
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

    # 1. Inspect URL parameters
    for k, v in request.args.items():
        is_threat, threat_type, matched = scan_threats(f"{k} {v}")
        if is_threat:
            log_security_event(threat_type, f"GET query param: {k}={matched}", source="WAF_URL")
            abort(403, "Access Denied: Malicious activity detected by SBG Cyber Defense Shield.")

    # 2. Inspect Form submissions
    if request.form:
        for k, v in request.form.items():
            if k in ("password", "old_password", "new_password", "confirm_password"):
                continue  # Passwords may contain arbitrary chars
            is_threat, threat_type, matched = scan_threats(str(v))
            if is_threat:
                log_security_event(threat_type, f"POST form field: {k}={matched}", source="WAF_FORM")
                abort(403, "Access Denied: Malicious request payload blocked by SBG Cyber Defense Shield.")

    # 3. Inspect JSON payloads
    if request.is_json:
        data = request.get_json(silent=True) or {}
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, str):
                    is_threat, threat_type, matched = scan_threats(v)
                    if is_threat:
                        log_security_event(threat_type, f"JSON payload: {k}={matched}", source="WAF_JSON")
                        # For API endpoints, return JSON 403
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
