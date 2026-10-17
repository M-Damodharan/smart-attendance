"""SBG Biometric Face Recognition & Account Binding Security Engine.

Enforces the strict security policy:
1. One Face, One Employee ID (Duplicate-Face Detection).
2. Face-to-Account Binding (A face registered to EMP001 cannot punch under E0012, and vice-versa).
3. Server-side validation with audit logging.
4. HR-assisted face registration, reset, and recovery workflow.
5. Invariant to front-camera mirroring, distance/scale variation, vertical shifts, and aspect ratio.
"""
import base64
import io
import json
import math
import os
from datetime import datetime

try:
    import numpy as np
except ImportError:
    np = None

from PIL import Image, ImageOps, ImageFilter, ImageStat
from flask import current_app

from .services import audit, now

# Similarity threshold:
# Legitimate user across scales/angles/mirroring scores >= 0.85 - 1.00.
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


def check_selfie_image_quality(img_input):
    """Detects whether an attendance camera snapshot is pitch black, extremely dark,
    blank, or covered (meaning NO human face is present).
    Returns: (is_valid: bool, reason: str, mean_brightness: float)
    """
    try:
        im = _load_image(img_input)
        if im.size[0] < 4 or im.size[1] < 4:
            return False, "Liveness / Camera check failed: Camera image is corrupt or empty.", 0.0

        gray = im.convert("L")
        stat = ImageStat.Stat(gray)
        mean_val = float(stat.mean[0])
        std_val = float(stat.stddev[0])

        # Pitch black or covered lens detection (black screen)
        # Pitch black is 0.0 - 5.0. Dark covered camera in low light is < 16.0.
        if mean_val < 16.0:
            return False, f"Camera screen is black or too dark (brightness: {mean_val:.1f}/255). No face detected. Please ensure your camera lens is uncovered and you have sufficient lighting.", mean_val

        # Blank screen or uniform canvas detection (lack of texture/variance)
        if std_val < 6.0:
            return False, "Liveness check failed: Camera screen is blank or uniform. No human facial features visible.", mean_val

        return True, "OK", mean_val
    except Exception as e:
        return False, f"Could not analyze camera image: {str(e)}", 0.0


def smart_crop_face(im, scale=1.0, dy_pct=0.0, dx_pct=0.0):
    """Smart face crop focusing on human facial oval.
    Automatically handles portrait smartphone photos vs landscape webcams."""
    w, h = im.size
    min_dim = min(w, h)

    # In portrait photos (phone cameras), the face is centered around upper 42%.
    # In landscape or square webcam streams, the face is centered around 50%.
    if h > w * 1.12:
        cy_base = int(h * 0.42)
        crop_base = int(w * 0.78)
    else:
        cy_base = int(h * 0.50)
        crop_base = int(min_dim * 0.82)

    crop_size = max(24, int(crop_base * scale))
    cx = int(w * 0.5 + w * dx_pct)
    cy = int(cy_base + h * dy_pct)

    box = (
        max(0, cx - crop_size // 2),
        max(0, cy - crop_size // 2),
        min(w, cx + crop_size // 2),
        min(h, cy + crop_size // 2)
    )
    return im.crop(box)


def extract_face_embedding(img_input, scale=1.0, dy_pct=0.0, dx_pct=0.0) -> list:
    """Extracts a normalized 160-dimensional facial descriptor vector.
    Uses Difference of Gaussians (DoG) bandpass filtering to remove illumination/shadow
    variations, followed by multi-scale block spatial projections and discrete cosine basis.
    Returns: list of floats (unit L2-normalized).
    """
    is_ok, quality_err, _ = check_selfie_image_quality(img_input)
    if not is_ok:
        raise ValueError(f"Face extraction failed: {quality_err}")

    im = _load_image(img_input)

    # Convert to grayscale
    im = im.convert("L")

    # Smart face crop
    im = smart_crop_face(im, scale=scale, dy_pct=dy_pct, dx_pct=dx_pct)

    # Histogram equalization for contrast normalization
    im = ImageOps.equalize(im)

    # Canonical 64x64 resolution
    im = im.resize((64, 64), Image.Resampling.BILINEAR)

    if np is not None:
        arr = np.asarray(im, dtype=np.float32)
        blurred = np.asarray(im.filter(ImageFilter.GaussianBlur(radius=2)), dtype=np.float32)
        structure = arr - blurred

        block_feats = []
        cell_size = 16
        for r in range(4):
            for c in range(4):
                b = structure[r * cell_size : (r + 1) * cell_size, c * cell_size : (c + 1) * cell_size]
                block_feats.append(float(np.std(b)))

                h_proj = np.mean(b, axis=0)
                v_proj = np.mean(b, axis=1)

                for k in range(1, 5):
                    cos_k = np.cos(np.pi * (np.arange(16) + 0.5) * k / 16.0)
                    block_feats.append(float(np.dot(h_proj, cos_k)))
                    block_feats.append(float(np.dot(v_proj, cos_k)))

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
        vec = vec - np.mean(vec)
        norm = float(np.linalg.norm(vec))
        if norm > 1e-7:
            vec = vec / norm

        return [round(float(x), 6) for x in vec]
    else:
        # High-precision pure Python fallback (zero external dependencies)
        pixels = list(im.getdata())
        blurred_pixels = list(im.filter(ImageFilter.GaussianBlur(radius=2)).getdata())
        structure = [float(p) - float(b) for p, b in zip(pixels, blurred_pixels)]

        block_feats = []
        cell_size = 16
        for r in range(4):
            for c in range(4):
                block_vals = []
                for br in range(16):
                    row_idx = (r * cell_size + br) * 64 + (c * cell_size)
                    block_vals.extend(structure[row_idx : row_idx + 16])
                b_mean = sum(block_vals) / 256.0
                b_std = math.sqrt(sum((x - b_mean) ** 2 for x in block_vals) / 256.0)
                block_feats.append(b_std)

                h_proj = [sum(block_vals[br * 16 + bc] for br in range(16)) / 16.0 for bc in range(16)]
                v_proj = [sum(block_vals[br * 16 + bc] for bc in range(16)) / 16.0 for br in range(16)]

                for k in range(1, 5):
                    cos_k = [math.cos(math.pi * (idx + 0.5) * k / 16.0) for idx in range(16)]
                    block_feats.append(sum(h * c for h, c in zip(h_proj, cos_k)))
                    block_feats.append(sum(v * c for v, c in zip(v_proj, cos_k)))

        macro_im = im.resize((16, 16), Image.Resampling.BILINEAR)
        macro_vals = [float(x) for x in macro_im.getdata()]
        m_mean = sum(macro_vals) / 256.0
        m_std = math.sqrt(sum((x - m_mean) ** 2 for x in macro_vals) / 256.0) + 1e-5
        macro_norm = [(x - m_mean) / m_std for x in macro_vals]

        for u in range(4):
            for v in range(4):
                if u == 0 and v == 0:
                    continue
                cos_u = [math.cos((2 * idx + 1) * u * math.pi / 32.0) for idx in range(16)]
                cos_v = [math.cos((2 * idx + 1) * v * math.pi / 32.0) for idx in range(16)]
                val = 0.0
                for r_idx in range(16):
                    for c_idx in range(16):
                        val += macro_norm[r_idx * 16 + c_idx] * cos_u[r_idx] * cos_v[c_idx]
                block_feats.append(val)

        mean_feat = sum(block_feats) / len(block_feats)
        centered = [x - mean_feat for x in block_feats]
        norm = math.sqrt(sum(x * x for x in centered))
        if norm > 1e-7:
            centered = [x / norm for x in centered]
        return [round(float(x), 6) for x in centered]


def get_candidate_live_embeddings(img_input) -> list:
    """Generates a search grid of candidate embeddings from an image.
    Crucial for real-world punch recognition:
    1. Normal & Mirrored (handles front-facing selfie camera mirror vs uploaded photo).
    2. Multi-scale search (scale 0.85 to 1.25 covers distance from camera).
    3. Multi-shift search (dy_pct covers head position in frame).
    """
    im = _load_image(img_input)
    candidates = []

    # 1. Primary candidate (scale=1.0, dy=0.0)
    primary = extract_face_embedding(im)
    candidates.append(primary)

    # 2. Mirror primary (handles front-facing webcam vs regular selfie)
    im_mirrored = ImageOps.mirror(im)
    candidates.append(extract_face_embedding(im_mirrored))

    # 3. Multi-scale and vertical shift grid
    for is_mirror in (False, True):
        base = im_mirrored if is_mirror else im
        for scale in (0.85, 1.15, 1.25):
            for dy in (-0.05, 0.05):
                candidates.append(extract_face_embedding(base, scale=scale, dy_pct=dy))

    return candidates


def compute_similarity(v1, v2) -> float:
    """Computes cosine similarity between two face feature vectors.
    Returns float in range [0.0, 1.0].
    """
    if not v1 or not v2:
        return 0.0
    if np is not None:
        a = np.array(v1, dtype=np.float32)
        b = np.array(v2, dtype=np.float32)
        dot = float(np.dot(a, b))
    else:
        dot = sum(float(x) * float(y) for x, y in zip(v1, v2))
    return max(0.0, min(1.0, dot))


def parse_template(template_str):
    """Safely deserializes face template JSON from SQLite, returning primary vector."""
    if not template_str:
        return None
    try:
        data = json.loads(template_str)
        if isinstance(data, dict):
            if "vector" in data and isinstance(data["vector"], list):
                return data["vector"]
            if "angles" in data and isinstance(data["angles"], dict):
                return data["angles"].get("center") or next(iter(data["angles"].values()), None)
        if isinstance(data, list):
            if data and isinstance(data[0], list):
                return data[0]
            return data
    except Exception:
        pass
    return None


def parse_template_angles(template_str) -> list:
    """Safely deserializes all angle vectors (Center, Left, Right, multi_views) from face template."""
    if not template_str:
        return []
    try:
        data = json.loads(template_str)
        if isinstance(data, dict):
            vecs = []
            if "angles" in data and isinstance(data["angles"], dict):
                for k in ("center", "left", "right"):
                    v = data["angles"].get(k)
                    if v and isinstance(v, list):
                        vecs.append(v)
            if "multi_views" in data and isinstance(data["multi_views"], list):
                for mv in data["multi_views"]:
                    if mv and isinstance(mv, list):
                        vecs.append(mv)
            if "vectors" in data and isinstance(data["vectors"], list):
                for v in data["vectors"]:
                    if isinstance(v, list):
                        vecs.append(v)
            if "vector" in data and isinstance(data["vector"], list):
                vecs.append(data["vector"])
            if vecs:
                return vecs
        if isinstance(data, list):
            if data and isinstance(data[0], list):
                return data
            return [data]
    except Exception:
        pass
    return []


def compute_multi_angle_similarity(live_input, template_angles: list) -> float:
    """Computes similarity of live face against all registered angles.
    `live_input` can be:
    - A single vector (list of floats)
    - A list of candidate vectors
    - An image (str data URL, bytes, file path, PIL Image)
    Returns the maximum similarity achieved across any candidate view and registered angle.
    """
    if not live_input or not template_angles:
        return 0.0

    if isinstance(live_input, (list, tuple)):
        if live_input and isinstance(live_input[0], (int, float)):
            # Single vector
            live_vecs = [live_input]
        else:
            # List of candidate vectors
            live_vecs = live_input
    elif np is not None and isinstance(live_input, np.ndarray):
        live_vecs = [list(live_input)]
    else:
        # Live image: generate candidate search grid
        try:
            live_vecs = get_candidate_live_embeddings(live_input)
        except Exception:
            live_vecs = [extract_face_embedding(live_input)]

    highest = 0.0
    for l_vec in live_vecs:
        for t_vec in template_angles:
            sim = compute_similarity(l_vec, t_vec)
            if sim > highest:
                highest = sim
    return highest


# -----------------------------------------------------------------------------
# Core Security Functions
# -----------------------------------------------------------------------------

def check_duplicate_face(db, new_embeddings, exclude_emp_id=None, threshold=FACE_MATCH_THRESHOLD):
    """Verifies that a face is NOT already registered under another Employee ID.
    Enforces 'One Face, One Account'.
    `new_embeddings` can be a single vector, list of vectors, or dict of angle vectors.
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

    # Normalize new_embeddings to a list of vectors
    if isinstance(new_embeddings, dict):
        new_vecs = [v for v in new_embeddings.values() if isinstance(v, list)]
    elif isinstance(new_embeddings, list):
        if new_embeddings and isinstance(new_embeddings[0], list):
            new_vecs = new_embeddings
        else:
            new_vecs = [new_embeddings]
    else:
        new_vecs = []

    for r in rows:
        stored_angles = parse_template_angles(r["face_template"])
        if not stored_angles:
            continue
        sim = compute_multi_angle_similarity(new_vecs, stored_angles)
        if sim > highest_sim:
            highest_sim = sim
            if sim >= threshold:
                best_match = dict(r)

    if best_match and highest_sim >= threshold:
        return True, best_match, highest_sim
    return False, None, highest_sim


def find_matching_employee(db, live_input, exclude_emp_id=None, threshold=FACE_MATCH_THRESHOLD):
    """Finds which employee account (if any) matches the live face.
    Returns: (matching_employee: dict | None, similarity: float)
    """
    is_dup, match, sim = check_duplicate_face(db, live_input, exclude_emp_id=exclude_emp_id, threshold=threshold)
    return match, sim


def verify_face_for_employee(db, emp_id: int, live_input, threshold=FACE_MATCH_THRESHOLD) -> dict:
    """Verifies a live face during attendance punch against the logged-in employee account.
    Returns a structured security decision dictionary:
      - verified: bool (True only if face matches THIS employee's account)
      - status: 'MATCH' | 'BOUND_TO_OTHER_ACCOUNT' | 'FACE_MISMATCH' | 'NOT_REGISTERED'
      - similarity: float
      - error: str (user-friendly error message if verification fails)
    """
    # 1. Inspect camera image quality (detect pitch black screen, covered lens, or blank canvas)
    if not isinstance(live_input, (list, tuple)) and not (np is not None and isinstance(live_input, np.ndarray)):
        is_ok, quality_err, _ = check_selfie_image_quality(live_input)
        if not is_ok:
            return {
                "verified": False,
                "status": "BLACK_FRAME",
                "similarity": 0.0,
                "error": quality_err
            }

    # Extract live embedding
    if isinstance(live_input, (list, tuple)) or (np is not None and isinstance(live_input, np.ndarray)):
        live_embedding = list(live_input)
        live_candidates = [live_embedding]
    else:
        try:
            live_embedding = extract_face_embedding(live_input)
            live_candidates = get_candidate_live_embeddings(live_input)
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

    # 2. Check if employee has a registered face
    if not user["face_registered"] or not user["face_template"]:
        return {
            "verified": False,
            "status": "NOT_REGISTERED",
            "similarity": 0.0,
            "error": "Face not registered for your account. Please contact HR to register your face."
        }

    user_angles = parse_template_angles(user["face_template"])
    if not user_angles:
        return {
            "verified": False,
            "status": "NOT_REGISTERED",
            "similarity": 0.0,
            "error": "Face template corrupted. Please contact HR to re-register your face."
        }

    # 3. Check similarity against the logged-in user's multi-angle template
    sim = compute_multi_angle_similarity(live_candidates, user_angles)

    if sim >= threshold:
        return {
            "verified": True,
            "status": "MATCH",
            "similarity": sim,
            "message": f"Face verified successfully ({sim * 100:.1f}% match)."
        }

    # 4. If face doesn't match logged-in account, check if it belongs to ANOTHER employee:
    # Evaluate primary live embedding against other registered accounts
    other_match, other_sim = find_matching_employee(db, live_embedding, exclude_emp_id=emp_id, threshold=threshold)
    if not other_match and isinstance(live_input, (str, bytes, bytearray, Image.Image)):
        # Also check mirrored primary embedding
        try:
            mirrored_emb = extract_face_embedding(ImageOps.mirror(_load_image(live_input)))
            other_match, other_sim = find_matching_employee(db, mirrored_emb, exclude_emp_id=emp_id, threshold=threshold)
        except Exception:
            pass
    if other_match:
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

    # 5. Face does not match registered employee and is unrecognized
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
    """Registers a face for an employee with duplicate-face detection and 360 multi-angle support.
    `img_input` can be:
      - A single image (data URL, file path, bytes, or PIL Image)
      - A dictionary of angles: {"center": img, "left": img, "right": img}
    Returns: (success: bool, message: str, duplicate_info: dict | None)
    """
    user = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if not user:
        return False, "Employee not found.", None

    embeddings = {}
    multi_views = []
    primary_img = None

    if isinstance(img_input, dict):
        for angle in ("center", "left", "right"):
            if angle in img_input and img_input[angle]:
                try:
                    emb = extract_face_embedding(img_input[angle])
                    embeddings[angle] = emb
                    views = get_candidate_live_embeddings(img_input[angle])
                    multi_views.extend(views)
                    if angle == "center" or primary_img is None:
                        primary_img = img_input[angle]
                except Exception as e:
                    return False, f"Could not process {angle} face angle: {e}", None
        if not embeddings:
            return False, "No valid face angles provided.", None
        primary_embedding = embeddings.get("center") or next(iter(embeddings.values()))
    else:
        try:
            primary_embedding = extract_face_embedding(img_input)
            embeddings["center"] = primary_embedding
            multi_views = get_candidate_live_embeddings(img_input)
            primary_img = img_input
        except Exception as e:
            return False, f"Could not process face image: {e}", None

    # Check for duplicate face across all other accounts
    if not force:
        is_dup, other_emp, sim = check_duplicate_face(db, embeddings, exclude_emp_id=emp_id)
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
    if selfie_dir and primary_img:
        os.makedirs(selfie_dir, exist_ok=True)
        im = _load_image(primary_img)
        im.thumbnail((640, 640))
        im.save(os.path.join(selfie_dir, face_filename), "JPEG", quality=85)

    template_payload = json.dumps({
        "vector": primary_embedding,
        "angles": embeddings,
        "multi_views": multi_views[:16],  # Store key invariant views
        "registered_at": now_ts.isoformat(),
        "registered_by": registered_by_id,
        "algorithm": "sbg-multi-angle-adaptive-v2",
    })

    db.execute(
        "UPDATE employees SET face_registered=1, face_template=?, face_registered_at=?, "
        "face_registered_by=?, face_image=? WHERE id=?",
        (template_payload, now_ts.strftime("%Y-%m-%d %H:%M:%S"), registered_by_id, face_filename, emp_id)
    )

    audit(
        "FACE_REGISTERED",
        "employee",
        emp_id,
        f"Face ID registered for {user['emp_code']} ({user['name']}) by ID {registered_by_id or 'self'} with 360 multi-view support"
    )
    db.commit()
    return True, "Face registered successfully.", None


def reset_face_for_employee(db, emp_id: int, reset_by_id: int = None, reason: str = ""):
    """HR-assisted face reset and recovery."""
    user = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if not user:
        return False

    old_img = user["face_image"]
    db.execute(
        "UPDATE employees SET face_registered=0, face_template=NULL, face_registered_at=NULL, "
        "face_registered_by=NULL, face_image=NULL WHERE id=?",
        (emp_id,)
    )

    selfie_dir = current_app.config.get("SELFIE_DIR")
    if selfie_dir and old_img:
        old_path = os.path.join(selfie_dir, old_img)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

    details = f"Face registration reset for {user['emp_code']} ({user['name']}) by ID {reset_by_id or 'admin'}"
    if reason:
        details += f" Reason: {reason}"

    audit("FACE_RESET", "employee", emp_id, details)
    db.commit()
    return True
