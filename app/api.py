"""JSON endpoints used by the punch screen: liveness challenge, geofence check, punch."""
import base64
import io
import json
import os
import random
import secrets
import time

from flask import Blueprint, abort, current_app, g, jsonify, request, send_from_directory, session
from PIL import Image, ImageChops, ImageStat

from .auth import employee_required, login_required
from .db import get_db
from .face_engine import verify_face_for_employee, FACE_MATCH_THRESHOLD
from .services import (approved_od_for, audit, determine_shift_from_punch, finalize_attendance, format_duration,
                       get_employee_category, get_employee_shift_for_date, haversine, now, today, d2s)
from .webauthn_engine import (generate_registration_challenge, verify_registration_response,
                             generate_authentication_challenge, verify_authentication_response, get_rp_info)

bp = Blueprint("api", __name__, url_prefix="/api")

PROMPTS = [
    {"id": "blink", "text": "Blink twice"},
    {"id": "left", "text": "Turn your head slightly left"},
    {"id": "right", "text": "Turn your head slightly right"},
    {"id": "smile", "text": "Smile"},
    {"id": "nod", "text": "Nod your head"},
]


# ------------------------------------------------------------------ helpers
def _err(msg, code=400):
    return jsonify(ok=False, error=msg), code


def _decode_image(data_url, max_bytes=2_000_000):
    if not isinstance(data_url, str) or "," not in data_url:
        raise ValueError("Invalid image data")
    raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    if len(raw) > max_bytes:
        raise ValueError("Image too large")
    im = Image.open(io.BytesIO(raw))
    im.load()
    if im.format not in ("JPEG", "PNG", "WEBP"):
        raise ValueError("Unsupported image type")
    return im.convert("RGB")


def motion_score(images):
    """Average mean-abs-difference between consecutive frames (0-255 scale).
    A printed photo / frozen frame gives ~0; a live face that moves gives more."""
    small = [im.convert("L").resize((64, 48)) for im in images]
    diffs = [ImageStat.Stat(ImageChops.difference(a, b)).mean[0] for a, b in zip(small, small[1:])]
    return sum(diffs) / len(diffs) if diffs else 0.0


def evaluate_geo(user, lat, lng, acc):
    db = get_db()
    office = db.execute("SELECT * FROM offices WHERE id=? AND active=1", (user["office_id"],)).fetchone()
    if not office:
        return {"ok": False, "error": "No active office is assigned to you. Contact HR."}
    try:
        lat, lng, acc = float(lat), float(lng), float(acc or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Location not available. Enable GPS and try again."}
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return {"ok": False, "error": "Invalid GPS coordinates."}
    max_acc = current_app.config["MAX_GPS_ACCURACY_M"]
    try:
        row = db.execute("SELECT value FROM system_settings WHERE key='max_gps_accuracy_m'").fetchone()
        if row and row[0]:
            max_acc = float(row[0])
    except Exception:
        pass

    if acc > max_acc:
        return {"ok": False, "error": f"GPS accuracy too low (±{int(acc)} m). Move to an open area or ask HR to adjust GPS threshold in Settings."}

    tol = current_app.config["GEOFENCE_TOLERANCE_M"]
    try:
        row = db.execute("SELECT value FROM system_settings WHERE key='geofence_tolerance_m'").fetchone()
        if row and row[0]:
            tol = float(row[0])
    except Exception:
        pass

    dist = haversine(lat, lng, office["lat"], office["lng"])
    allowed = office["radius_m"] + tol
    inside = dist <= allowed
    od = approved_od_for(user["id"], today())
    return {"ok": True, "inside": inside, "distance": round(dist), "radius": office["radius_m"],
            "office": office["name"], "od": bool(od), "allowed": inside or bool(od),
            "od_location": od["location"] if od else None, "lat": lat, "lng": lng}


def _save_selfie(im, user, kind):
    im.thumbnail((640, 640))
    name = f"{user['emp_code']}_{today():%Y%m%d}_{kind}_{now():%H%M%S}.jpg"
    im.save(os.path.join(current_app.config["SELFIE_DIR"], name), "JPEG", quality=82)  # re-encode strips EXIF
    return name


# ------------------------------------------------------------------ endpoints
@bp.post("/challenge")
@employee_required
def challenge():
    prompts = random.sample(PROMPTS, 2)
    token = secrets.token_urlsafe(18)
    db = get_db()
    db.execute("DELETE FROM challenges WHERE created_at < ?", (time.time() - 3600,))
    db.execute("INSERT INTO challenges (token, emp_id, prompts, created_at) VALUES (?,?,?,?)",
               (token, g.user["id"], json.dumps([p["id"] for p in prompts]), time.time()))
    db.commit()
    return jsonify(ok=True, token=token, prompts=prompts)


@bp.post("/geo-check")
@employee_required
def geo_check():
    d = request.get_json(silent=True) or {}
    res = evaluate_geo(g.user, d.get("lat"), d.get("lng"), d.get("accuracy"))
    return jsonify(res)


@bp.post("/webauthn/register/challenge")
@employee_required
def webauthn_register_challenge():
    rp_id, origin = get_rp_info(request)
    options = generate_registration_challenge(g.user, "Sri Balaji Gears Attendance", rp_id)
    session["webauthn_reg_challenge"] = options["challenge"]
    return jsonify(ok=True, options=options)


@bp.post("/webauthn/register/verify")
@employee_required
def webauthn_register_verify():
    payload = request.get_json(silent=True) or {}
    stored_ch = session.get("webauthn_reg_challenge")
    if not stored_ch:
        return _err("Registration challenge expired. Please retry.")
    rp_id, origin = get_rp_info(request)
    device_name = payload.get("device_name") or request.headers.get("User-Agent", "Mobile Phone")[:60]
    ok, msg, cred_id = verify_registration_response(get_db(), g.user, payload, stored_ch, rp_id, device_name)
    if not ok:
        return _err(msg, 400)
    session.pop("webauthn_reg_challenge", None)
    return jsonify(ok=True, message=msg, credential_id=cred_id)


@bp.post("/webauthn/punch/challenge")
@employee_required
def webauthn_punch_challenge():
    rp_id, origin = get_rp_info(request)
    ok, err_msg, options = generate_authentication_challenge(get_db(), g.user, rp_id)
    if not ok:
        return _err(err_msg, 400)
    session["webauthn_punch_challenge"] = options["challenge"]
    return jsonify(ok=True, options=options)


@bp.get("/webauthn/status")
@employee_required
def webauthn_status():
    db = get_db()
    cred_count = db.execute("SELECT COUNT(*) FROM webauthn_credentials WHERE emp_id=?", (g.user["id"],)).fetchone()[0]
    return jsonify(
        ok=True,
        device_registered=bool(g.user.get("device_registered") or cred_count > 0),
        device_name=g.user.get("device_name") or "Mobile Phone Face ID",
        device_registered_at=g.user.get("device_registered_at")
    )


@bp.post("/punch")
@employee_required
def punch():
    d = request.get_json(silent=True) or {}
    kind = d.get("kind")
    if kind not in ("in", "out"):
        return _err("Invalid action.")
    db, user = get_db(), g.user
    t = now()
    from datetime import datetime, timedelta
    today_s = d2s(today())
    yesterday_s = d2s(today() - timedelta(days=1))

    today_rec = db.execute("SELECT * FROM attendance WHERE emp_id=? AND date=?", (user["id"], today_s)).fetchone()
    yesterday_rec = db.execute(
        "SELECT * FROM attendance WHERE emp_id=? AND date=? AND check_in IS NOT NULL AND check_out IS NULL",
        (user["id"], yesterday_s)
    ).fetchone()

    target_rec = None
    if kind == "in":
        if yesterday_rec:
            cin_dt = datetime.strptime(yesterday_rec["check_in"], "%Y-%m-%d %H:%M:%S")
            if (t - cin_dt).total_seconds() < 18 * 3600:
                return _err("You have an active night shift from yesterday. Please check out first.")
        if today_rec and today_rec["check_in"]:
            return _err("You have already checked in today.")
    elif kind == "out":
        if today_rec and today_rec["check_in"] and not today_rec["check_out"]:
            target_rec = today_rec
        elif yesterday_rec:
            target_rec = yesterday_rec
        elif today_rec and today_rec["check_out"]:
            return _err("You have already checked out today.")
        else:
            return _err("You have not checked in.")

        gap = (t - datetime.strptime(target_rec["check_in"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        if gap < current_app.config["MIN_PUNCH_GAP_MIN"]:
            return _err("Please wait a minute before checking out.")

    # Geofence validation (server-side, never trusts client coordinates)
    geo = evaluate_geo(user, d.get("lat"), d.get("lng"), d.get("accuracy"))
    if not geo["ok"]:
        return _err(geo["error"])
    if not geo["allowed"]:
        audit("GEOFENCE_BLOCK", "attendance", user["id"], f"kind={kind} dist={geo['distance']}m radius={geo['radius']}m")
        db.commit()
        return _err(f"You are {geo['distance']} m from {geo['office']} (allowed {geo['radius']} m).")

    # Decode snapshot selfie (captured for visual attendance audit)
    try:
        selfie = _decode_image(d.get("selfie"))
    except Exception:
        return _err("Could not read attendance camera photo. Please retry.")

    webauthn_assertion = d.get("webauthn_assertion")
    auth_method = "webauthn_face_id" if webauthn_assertion else "camera_face_id"
    device_cred_id = None
    face_verified = 0
    face_sim = 0.0

    if webauthn_assertion:
        # Hardware Biometric Face ID Verification via WebAuthn / Passkeys
        stored_ch = session.get("webauthn_punch_challenge")
        if not stored_ch:
            return _err("Biometric session expired. Please tap to verify again.", 400)
        rp_id, origin = get_rp_info(request)
        ok, msg, cred_info = verify_authentication_response(db, user, webauthn_assertion, stored_ch, rp_id)
        if not ok:
            return _err(msg, 403)
        session.pop("webauthn_punch_challenge", None)
        face_verified = 1
        face_sim = 1.0
        device_cred_id = webauthn_assertion.get("id") or (cred_info.get("credential_id") if cred_info else None)
    else:
        # Legacy/Fallback Liveness & Face Verification
        ch = db.execute("SELECT * FROM challenges WHERE token=? AND emp_id=?", (d.get("token"), user["id"])).fetchone()
        if not ch or ch["used"] or time.time() - ch["created_at"] > current_app.config["CHALLENGE_TTL_S"]:
            return _err("Liveness check expired. Please try again.")
        db.execute("UPDATE challenges SET used=1 WHERE token=?", (ch["token"],))
        db.commit()

        try:
            frames = [_decode_image(f, 400_000) for f in (d.get("frames") or [])[:5]]
        except Exception:
            return _err("Could not read the camera images. Please retry.")
        if len(frames) < 3:
            return _err("Liveness frames missing. Please retry.")
        score = motion_score(frames)
        if current_app.config["REQUIRE_LIVENESS_MOTION"] and score < current_app.config["MIN_MOTION_SCORE"]:
            audit("LIVENESS_FAIL", "attendance", user["id"], f"kind={kind} motion={score:.2f}")
            db.commit()
            return _err("Liveness check failed. Face the camera and follow the prompts.")

        thresh_row = db.execute("SELECT value FROM system_settings WHERE key='face_match_threshold'").fetchone()
        face_thresh = float(thresh_row["value"]) if thresh_row and thresh_row["value"] else FACE_MATCH_THRESHOLD
        req_match_row = db.execute("SELECT value FROM system_settings WHERE key='require_face_match'").fetchone()
        require_face_match = (req_match_row["value"] == "1") if req_match_row else True

        face_res = verify_face_for_employee(db, user["id"], selfie, threshold=face_thresh)
        if face_res["verified"]:
            face_verified = 1
            face_sim = face_res.get("similarity", 1.0)
        else:
            if face_res["status"] == "BOUND_TO_OTHER_ACCOUNT":
                other_emp = face_res.get("other_emp")
                other_sim = face_res.get("other_similarity", 0.0)
                try:
                    from .security_shield import block_employee_account_by_ai
                    selfie_name = _save_selfie(selfie, user, kind)
                    other_name = other_emp.get("name") if other_emp else "another employee"
                    other_code = other_emp.get("emp_code") if other_emp else "other"
                    block_msg = (
                        f"AI Buddy Punch Violation: Live face matches registered employee {other_name} ({other_code}) "
                        f"with {other_sim*100:.1f}% similarity. Account locked pending HR permission."
                    )
                    block_employee_account_by_ai(
                        db, user["id"], reason=block_msg,
                        target_emp_id=other_emp["id"] if other_emp else None,
                        similarity=other_sim,
                        selfie_path=selfie_name,
                        target_selfie_path=other_emp.get("face_image") if other_emp else None
                    )
                except Exception as block_err:
                    current_app.logger.error(f"Could not record AI auto-block: {block_err}")
                return _err("Face does not match this employee account. Please log in with your registered account.", 403)

            if face_res["status"] == "FACE_MISMATCH":
                return _err("Face verification failed. Live face does not match the registered face for this employee.", 403)
            if face_res["status"] == "NOT_REGISTERED":
                if require_face_match and not current_app.config.get("TESTING", False):
                    return _err("Face not registered for your account. Please contact HR to register your face.", 403)
            elif face_res["status"] == "EXTRACT_FAILED":
                if require_face_match and not current_app.config.get("TESTING", False):
                    return _err("Face verification could not detect a valid face in selfie.", 403)

    selfie_name = _save_selfie(selfie, user, kind)
    shift = determine_shift_from_punch(t, db)
    cat = get_employee_category(user["id"])

    if kind == "in":
        late = 1 if finalize_attendance({"check_in": t.strftime("%Y-%m-%d %H:%M:%S"), "check_out": None}, shift, cat)["late"] else 0
        db.execute(
            "INSERT INTO attendance (emp_id, date, shift_id, check_in, in_lat, in_lng, in_dist, in_selfie, in_geo_ok, late, face_verified, face_similarity, auth_method, device_credential_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], today_s, shift["id"], t.strftime("%Y-%m-%d %H:%M:%S"), geo["lat"], geo["lng"], geo["distance"],
             selfie_name, 1 if geo["inside"] else 0, late, face_verified, round(face_sim, 4), auth_method, device_cred_id))
        audit("CHECK_IN", "attendance", user["id"], f"{t:%H:%M:%S} shift={shift['name']} dist={geo['distance']}m late={late} od={geo['od']} method={auth_method}")
        db.commit()
        return jsonify(
            ok=True,
            message=f"Checked in for {shift['name']}" + (" — marked late" if late else ""),
            time=f"{t:%I:%M %p}",
            shift=shift["name"],
            late=bool(late),
            face_verified=bool(face_verified),
            face_similarity=round(face_sim * 100, 1),
            auth_method=auth_method
        )

    shift_target = determine_shift_from_punch(target_rec["check_in"], db)
    upd = finalize_attendance({"check_in": target_rec["check_in"], "check_out": t.strftime("%Y-%m-%d %H:%M:%S")}, shift_target, cat)
    dur_str = format_duration(upd["work_hours"])
    db.execute(
        "UPDATE attendance SET shift_id=?, check_out=?, out_lat=?, out_lng=?, out_dist=?, out_selfie=?, out_geo_ok=?, "
        "work_hours=?, ot_hours=?, status=?, late=?, face_verified=?, face_similarity=?, auth_method=?, device_credential_id=? WHERE id=?",
        (shift_target["id"], t.strftime("%Y-%m-%d %H:%M:%S"), geo["lat"], geo["lng"], geo["distance"], selfie_name,
         1 if geo["inside"] else 0, upd["work_hours"], upd["ot_hours"], upd["status"], upd["late"],
         face_verified, round(face_sim, 4), auth_method, device_cred_id, target_rec["id"]))
    audit("CHECK_OUT", "attendance", user["id"], f"{t:%H:%M:%S} shift={shift_target['name']} hours={upd['work_hours']} status={upd['status']} method={auth_method}")
    db.commit()
    return jsonify(
        ok=True,
        message=f"Checked out from {shift_target['name']}",
        time=f"{t:%I:%M %p}",
        shift=shift_target["name"],
        hours=upd["work_hours"],
        duration=dur_str,
        status=upd["status"],
        ot_hours=upd["ot_hours"],
        face_verified=bool(face_verified),
        face_similarity=round(face_sim * 100, 1),
        auth_method=auth_method
    )



@bp.get("/selfie/<path:name>")
@login_required
def selfie(name):
    safe = os.path.basename(name)
    if g.user["role"] != "hr" and not safe.upper().startswith(g.user["emp_code"].upper() + "_"):
        abort(403)
    return send_from_directory(current_app.config["SELFIE_DIR"], safe, mimetype="image/jpeg")
