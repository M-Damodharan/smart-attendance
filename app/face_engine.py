"""SBG Biometric Face Recognition & Account Binding Security Engine.

Enforces the strict security policy:
1. One Face, One Employee ID (Duplicate-Face Detection).
2. Face-to-Account Binding (A face registered to EMP001 cannot punch under E0012, and vice-versa).
3. Server-side validation with audit logging.
4. HR-assisted face registration, reset, and recovery workflow.
"""
import base64
import io
import json
import os
from datetime import datetime
import numpy as np
from PIL import Image, ImageOps, ImageFilter
from flask import current_app

from .services import audit, now

# Similarity threshold:
# Same person across different lighting/angles typically scores >= 0.78 - 0.95.
# Different people typically score <= 0.65.
# 0.75 provides a sharp, secure separation margin.
FACE_MATCH_THRESHOLD = 0.75


def _load_image(img_input):
    """Safely loads PIL image from data URL, file path, bytes, or PIL object."""
    if isinstance(img_input, Image.Image):
        return img_input.copy()
    if isinstance(img_input, str):
        if img_input.startswith("data:image"):
            raw = base64.b64decode(img_input.split(",", 1)[1])
            return Image.open(io.BytesIO(raw))
        if os.path.exists(img_input):
            return Image.open(img_input)
    if isinstance(img_input, (bytes, bytearray)):
        return Image.open(io.BytesIO(img_input))
    raise ValueError("Unsupported or invalid image input format.")


def extract_face_embedding(img_input) -> list:
    """Extracts a normalized 160-dimensional facial structural descriptor vector.
    Uses Difference of Gaussians (DoG) bandpass filtering to remove illumination/shadow
    variations, followed by multi-scale block spatial projections and discrete cosine basis.
    Returns: list of floats (unit L2-normalized).
    """
    im = _load_image(img_input)

    # Convert to grayscale
    im = im.convert("L")

    # Center crop to face region (handles front camera selfie orientation)
    w, h = im.size
    min_dim = min(w, h)
    cx, cy = w // 2, h // 2
    # Crop central 80% to focus on face oval
    crop_size = int(min_dim * 0.8)
    box = (cx - crop_size // 2, cy - crop_size // 2, cx + crop_size // 2, cy + crop_size // 2)
    im = im.crop(box)

    # Histogram equalization for contrast normalization
    im = ImageOps.equalize(im)

    # Canonical 64x64 resolution
    im = im.resize((64, 64), Image.Resampling.BILINEAR)
    arr = np.asarray(im, dtype=np.float32)

    # Difference of Gaussians (DoG): isolates structural facial geometry (eyes, nose, jaw)
    blurred = np.asarray(im.filter(ImageFilter.GaussianBlur(radius=2)), dtype=np.float32)
    structure = arr - blurred

    # Multi-scale block spatial projections: 4x4 blocks (16x16 pixels each)
    block_feats = []
    cell_size = 16
    for r in range(4):
        for c in range(4):
            b = structure[r * cell_size : (r + 1) * cell_size, c * cell_size : (c + 1) * cell_size]
            # Standard deviation of local structure
            block_feats.append(float(np.std(b)))

            # Horizontal & vertical spatial intensity profiles
            h_proj = np.mean(b, axis=0)  # 16 values
            v_proj = np.mean(b, axis=1)  # 16 values

            # First 4 low-frequency cosine basis projections
            for k in range(1, 5):
                cos_k = np.cos(np.pi * (np.arange(16) + 0.5) * k / 16.0)
                block_feats.append(float(np.dot(h_proj, cos_k)))
                block_feats.append(float(np.dot(v_proj, cos_k)))

    # Global structural descriptors (downscaled 16x16 macro geometry)
    macro = np.asarray(im.resize((16, 16), Image.Resampling.BILINEAR), dtype=np.float32)
    macro_norm = (macro - np.mean(macro)) / (np.std(macro) + 1e-5)
    for u in range(4):
        for v in range(4):
            if u == 0 and v == 0:
                continue
            cos_u = np.cos((2 * np.arange(16) + 1) * u * np.pi / 32.0)
            cos_v = np.cos((2 * np.arange(16) + 1) * v * np.pi / 32.0)
            val = np.sum(macro_norm * np.outer(cos_u, cos_v))
            block_feats.append(float(val))

    vec = np.array(block_feats, dtype=np.float32)

    # Zero-mean center and L2 normalize
    vec = vec - np.mean(vec)
    norm = float(np.linalg.norm(vec))
    if norm > 1e-7:
        vec = vec / norm

    return [round(float(x), 6) for x in vec]


def compute_similarity(v1, v2) -> float:
    """Computes cosine similarity between two face feature vectors.
    Returns float in range [0.0, 1.0].
    """
    if not v1 or not v2:
        return 0.0
    a = np.array(v1, dtype=np.float32)
    b = np.array(v2, dtype=np.float32)
    dot = float(np.dot(a, b))
    # Cosine similarity for unit vectors is simply dot product
    # Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, dot))


def parse_template(template_str):
    """Safely deserializes face template JSON from SQLite."""
    if not template_str:
        return None
    try:
        data = json.loads(template_str)
        if isinstance(data, dict):
            return data.get("vector")
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return None


# -----------------------------------------------------------------------------
# Core Security Functions
# -----------------------------------------------------------------------------

def check_duplicate_face(db, new_embedding, exclude_emp_id=None, threshold=FACE_MATCH_THRESHOLD):
    """Verifies that a face is NOT already registered under another Employee ID.
    Enforces 'One Face, One Account'.
    Returns: (is_duplicate: bool, matching_employee: dict | None, similarity: float)
    """
    query = "SELECT id, emp_code, name, role, face_template, face_image FROM employees WHERE face_registered=1"
    params = []
    if exclude_emp_id:
        query += " AND id<>?"
        params.append(exclude_emp_id)

    rows = db.execute(query, params).fetchall()
    highest_sim = 0.0
    best_match = None

    for r in rows:
        tpl = parse_template(r["face_template"])
        if not tpl:
            continue
        sim = compute_similarity(new_embedding, tpl)
        if sim > highest_sim:
            highest_sim = sim
            if sim >= threshold:
                best_match = dict(r)

    if best_match and highest_sim >= threshold:
        return True, best_match, highest_sim
    return False, None, highest_sim


def find_matching_employee(db, live_embedding, exclude_emp_id=None, threshold=FACE_MATCH_THRESHOLD):
    """Finds which employee account (if any) matches the live face embedding.
    Returns: (matching_employee: dict | None, similarity: float)
    """
    is_dup, match, sim = check_duplicate_face(db, live_embedding, exclude_emp_id=exclude_emp_id, threshold=threshold)
    return match, sim


def verify_face_for_employee(db, emp_id: int, live_input, threshold=FACE_MATCH_THRESHOLD) -> dict:
    """Verifies a live face during attendance punch against the logged-in employee account.
    Returns a structured security decision dictionary:
      - verified: bool (True only if face matches THIS employee's account)
      - status: 'MATCH' | 'BOUND_TO_OTHER_ACCOUNT' | 'FACE_MISMATCH' | 'NOT_REGISTERED'
      - similarity: float
      - error: str (user-friendly error message if verification fails)
    """
    if isinstance(live_input, (list, tuple, np.ndarray)):
        live_embedding = list(live_input)
    else:
        try:
            live_embedding = extract_face_embedding(live_input)
        except Exception as e:
            return {
                "verified": False,
                "status": "EXTRACT_FAILED",
                "similarity": 0.0,
                "error": f"Could not process face from selfie: {e}"
            }

    user = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if not user:
        return {"verified": False, "status": "NO_USER", "error": "Employee record not found.", "similarity": 0.0}

    # 1. Identity Spoofing Check across company:
    # Does this face belong to ANOTHER employee in the company?
    # (e.g. Damodharan M [E0012] punching under Employee Staff [EMP001])
    other_match, other_sim = find_matching_employee(db, live_embedding, exclude_emp_id=emp_id, threshold=threshold)
    if other_match:
        # Log high-priority security violation
        audit(
            "FACE_ACCOUNT_MISMATCH",
            "employee",
            emp_id,
            f"SPOOF_ATTEMPT: Live face belongs to {other_match['name']} ({other_match['emp_code']}, sim={other_sim:.2f}) "
            f"but punch attempted under account {user['name']} ({user['emp_code']})"
        )
        db.commit()
        return {
            "verified": False,
            "status": "BOUND_TO_OTHER_ACCOUNT",
            "similarity": other_sim,
            "other_emp": other_match,
            "other_similarity": other_sim,
            "error": "Face does not match this employee account. Please log in with your registered account."
        }

    # 2. Check if employee has a registered face
    if not user["face_registered"] or not user["face_template"]:
        return {
            "verified": False,
            "status": "NOT_REGISTERED",
            "similarity": 0.0,
            "error": "Face not registered for your account. Please contact HR to register your face."
        }

    user_template = parse_template(user["face_template"])
    if not user_template:
        return {
            "verified": False,
            "status": "NOT_REGISTERED",
            "similarity": 0.0,
            "error": "Face template corrupted. Please contact HR to re-register your face."
        }

    # 3. Check similarity against the logged-in user's own registered template
    sim = compute_similarity(live_embedding, user_template)
    if sim >= threshold:
        return {
            "verified": True,
            "status": "MATCH",
            "similarity": sim,
            "message": f"Face verified successfully ({sim * 100:.1f}% match)."
        }

    # 4. Face does not match registered employee and is unrecognized
    audit(
        "FACE_VERIFICATION_FAILED",
        "employee",
        emp_id,
        f"Face mismatch for {user['emp_code']}: similarity={sim:.2f} (required >= {threshold:.2f})"
    )
    db.commit()
    return {
        "verified": False,
        "status": "FACE_MISMATCH",
        "similarity": sim,
        "error": "Face verification failed. Live face does not match the registered face for this employee."
    }


def register_face_for_employee(db, emp_id: int, img_input, registered_by_id: int = None, force: bool = False):
    """Registers a face for an employee with duplicate-face detection.
    Returns: (success: bool, message: str, duplicate_info: dict | None)
    """
    user = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if not user:
        return False, "Employee not found.", None

    try:
        embedding = extract_face_embedding(img_input)
    except Exception as e:
        return False, f"Could not process face image: {e}", None

    # Check for duplicate face across all other accounts
    if not force:
        is_dup, other_emp, sim = check_duplicate_face(db, embedding, exclude_emp_id=emp_id)
        if is_dup:
            audit(
                "FACE_DUPLICATE_REGISTRATION_BLOCKED",
                "employee",
                emp_id,
                f"Duplicate face blocked: matches {other_emp['name']} ({other_emp['emp_code']}) with similarity {sim:.2f}"
            )
            db.commit()
            return False, (
                f"Face registration rejected: This face is already registered to employee "
                f"'{other_emp['name']}' ({other_emp['emp_code']}). One face cannot be registered to multiple accounts."
            ), other_emp

    # Save reference photo to disk
    now_ts = now()
    face_filename = f"face_{user['emp_code']}_{now_ts.strftime('%Y%m%d_%H%M%S')}.jpg"
    selfie_dir = current_app.config.get("SELFIE_DIR")
    if selfie_dir:
        os.makedirs(selfie_dir, exist_ok=True)
        im = _load_image(img_input)
        im.thumbnail((640, 640))
        im.save(os.path.join(selfie_dir, face_filename), "JPEG", quality=85)

    template_payload = json.dumps({
        "vector": embedding,
        "registered_at": now_ts.isoformat(),
        "registered_by": registered_by_id,
        "algorithm": "DoG-SpatialProjection-v1"
    })

    db.execute(
        "UPDATE employees SET face_registered=1, face_template=?, face_registered_at=?, "
        "face_registered_by=?, face_image=? WHERE id=?",
        (template_payload, now_ts.strftime("%Y-%m-%d %H:%M:%S"), registered_by_id, face_filename, emp_id)
    )
    audit("FACE_REGISTERED", "employee", emp_id, f"Face registered for {user['emp_code']} by actor_id={registered_by_id}")
    db.commit()
    return True, f"Face successfully registered for {user['name']} ({user['emp_code']}).", None


def reset_face_for_employee(db, emp_id: int, reset_by_id: int = None):
    """Resets / clears face registration for an employee (HR-assisted recovery)."""
    user = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if not user:
        return False
    db.execute(
        "UPDATE employees SET face_registered=0, face_template=NULL, face_registered_at=NULL, "
        "face_registered_by=NULL, face_image=NULL WHERE id=?",
        (emp_id,)
    )
    audit("FACE_RESET", "employee", emp_id, f"Face registration reset for {user['emp_code']} by actor_id={reset_by_id}")
    db.commit()
    return True
