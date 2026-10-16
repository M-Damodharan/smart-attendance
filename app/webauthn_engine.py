"""
Phone Owner Face ID / Biometric Verification Engine (WebAuthn / Passkeys).
- Hardware-backed biometric authentication (Apple Face ID on iOS, Android Biometrics).
- Never stores or transmits raw face biometrics (biometrics remain inside the device Secure Enclave).
- Hardware cryptographic signatures verified using ECDSA (P-256 / ES256) or RSA (RS256).
- Strict One Device, One Account binding with AI Multi-Account Buddy Punch Defense.
"""

import base64
import hashlib
import io
import json
import os
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

import cbor2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from .services import audit, now


def b64url_encode(data: bytes) -> str:
    """Encode bytes to base64url string without trailing padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(data: str) -> bytes:
    """Decode base64url string to bytes, adding padding if required."""
    s = str(data).strip()
    # Add back padding if missing
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def get_rp_info(request) -> Tuple[str, str]:
    """Extract rp_id and expected_origin from Flask request."""
    host = request.host
    rp_id = host.split(":")[0]  # strip port
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme
    origin = f"{scheme}://{host}"
    return rp_id, origin


def generate_registration_challenge(user: Dict[str, Any], rp_name: str, rp_id: str) -> Dict[str, Any]:
    """Generate registration options for navigator.credentials.create()."""
    challenge_bytes = secrets.token_bytes(32)
    challenge_b64 = b64url_encode(challenge_bytes)
    user_id_b64 = b64url_encode(str(user["id"]).encode("utf-8"))

    options = {
        "rp": {
            "name": rp_name,
            "id": rp_id,
        },
        "user": {
            "id": user_id_b64,
            "name": user["emp_code"],
            "displayName": user["name"],
        },
        "challenge": challenge_b64,
        "pubKeyCredParams": [
            {"type": "public-key", "alg": -7},   # ES256 (ECDSA P-256) - default for iOS Face ID & Android
            {"type": "public-key", "alg": -257}, # RS256 (RSA 2048)
        ],
        "authenticatorSelection": {
            "authenticatorAttachment": "platform",  # Built-in Phone Face ID / Fingerprint
            "userVerification": "required",         # Enforces biometric Face ID
            "residentKey": "preferred",
        },
        "timeout": 60000,
        "attestation": "none",
    }
    return options


def _parse_cose_public_key(cose_data: Any) -> bytes:
    """Convert decoded COSE key dictionary to SubjectPublicKeyInfo PEM bytes."""
    kty = cose_data.get(1)
    if kty == 2:  # EC2 key
        crv = cose_data.get(-1)
        x_bytes = cose_data.get(-2)
        y_bytes = cose_data.get(-3)
        if crv != 1 or not x_bytes or not y_bytes:
            raise ValueError(f"Unsupported EC curve: {crv}")
        curve = ec.SECP256R1()
        pub_numbers = ec.EllipticCurvePublicNumbers(
            int.from_bytes(x_bytes, "big"),
            int.from_bytes(y_bytes, "big"),
            curve
        )
        pub_key = pub_numbers.public_key()
    elif kty == 3:  # RSA key
        n_bytes = cose_data.get(-1)
        e_bytes = cose_data.get(-2)
        if not n_bytes or not e_bytes:
            raise ValueError("Invalid RSA COSE key parameters")
        pub_numbers = rsa.RSAPublicNumbers(
            int.from_bytes(e_bytes, "big"),
            int.from_bytes(n_bytes, "big")
        )
        pub_key = pub_numbers.public_key()
    else:
        raise ValueError(f"Unsupported key type (kty): {kty}")

    return pub_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )


def verify_registration_response(
    db,
    user: Dict[str, Any],
    payload: Dict[str, Any],
    stored_challenge: str,
    expected_rp_id: str,
    device_name: Optional[str] = None
) -> Tuple[bool, str, Optional[str]]:
    """
    Verify client credentials.create() response and register device.
    Returns (success, message, credential_id).
    """
    try:
        raw_id_b64 = payload.get("id") or payload.get("rawId")
        if not raw_id_b64:
            return False, "Missing credential ID.", None

        resp = payload.get("response") or {}
        client_data_raw = b64url_decode(resp.get("clientDataJSON", ""))
        attestation_raw = b64url_decode(resp.get("attestationObject", ""))

        client_data = json.loads(client_data_raw.decode("utf-8"))
        if client_data.get("type") != "webauthn.create":
            return False, "Invalid WebAuthn client data type.", None

        if client_data.get("challenge") != stored_challenge:
            return False, "Registration challenge mismatch or expired.", None

        # Parse attestationObject (CBOR)
        att_obj = cbor2.loads(attestation_raw)
        auth_data = att_obj.get("authData")
        if not auth_data or len(auth_data) < 37:
            return False, "Invalid authenticator data.", None

        # Flags: byte 32
        flags = auth_data[32]
        user_present = bool(flags & 0x01)
        user_verified = bool(flags & 0x04)
        attested_cred = bool(flags & 0x40)

        if not user_present:
            return False, "User presence verification failed.", None

        if not attested_cred:
            return False, "Authenticator did not include attested credential data.", None

        # AAGUID (16 bytes)
        aaguid_bytes = auth_data[37:53]
        aaguid_str = aaguid_bytes.hex()

        # Credential ID
        cred_len = int.from_bytes(auth_data[53:55], "big")
        cred_id_bytes = auth_data[55:55 + cred_len]
        cred_id_b64 = b64url_encode(cred_id_bytes)

        # Parse COSE Public Key
        cose_stream = io.BytesIO(auth_data[55 + cred_len:])
        cose_key = cbor2.load(cose_stream)
        pem_bytes = _parse_cose_public_key(cose_key)
        pem_str = pem_bytes.decode("utf-8")

        # -------------------------------------------------------------------------
        # AI BUDDY PUNCH / MULTI-ACCOUNT POLICY ENFORCEMENT:
        # Check if this exact phone biometric credential ID is registered to another employee
        # -------------------------------------------------------------------------
        existing_other = db.execute(
            "SELECT w.*, e.emp_code, e.name FROM webauthn_credentials w "
            "JOIN employees e ON e.id = w.emp_id "
            "WHERE w.credential_id = ? AND w.emp_id != ?",
            (cred_id_b64, user["id"])
        ).fetchone()

        if existing_other:
            # Trigger AI Auto-Block
            try:
                from .security_shield import block_employee_account_by_ai
                block_msg = (
                    f"AI Multi-Account Violation: This phone's Face ID / Biometric key is already registered "
                    f"to employee {existing_other['name']} ({existing_other['emp_code']}). "
                    f"One Device, One Account policy strictly enforced."
                )
                block_employee_account_by_ai(
                    db,
                    user["id"],
                    reason=block_msg,
                    target_emp_id=existing_other["emp_id"],
                    similarity=1.0,
                )
            except Exception as block_err:
                pass
            return False, "This phone is already registered to another employee. One Phone, One Account policy enforced.", None

        # Check if user already has this credential registered
        existing_self = db.execute(
            "SELECT id FROM webauthn_credentials WHERE credential_id = ? AND emp_id = ?",
            (cred_id_b64, user["id"])
        ).fetchone()

        if not existing_self:
            db.execute(
                "INSERT INTO webauthn_credentials (emp_id, credential_id, public_key, sign_count, device_name, aaguid) "
                "VALUES (?,?,?,?,?,?)",
                (user["id"], cred_id_b64, pem_str, 0, device_name or "Mobile Phone Face ID", aaguid_str)
            )

        # Mark employee as having registered their phone Face ID
        reg_time = now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "UPDATE employees SET device_registered=1, device_name=?, device_registered_at=?, face_registered=1, face_registered_at=? WHERE id=?",
            (device_name or "Phone Face ID", reg_time, reg_time, user["id"])
        )
        audit("WEBAUTHN_DEVICE_REGISTERED", "webauthn", user["id"], f"Device={device_name or 'Phone Face ID'} UV={user_verified}")
        db.commit()

        return True, "Phone Face ID successfully registered and secured.", cred_id_b64

    except Exception as e:
        return False, f"Device registration error: {str(e)}", None


def generate_authentication_challenge(
    db,
    user: Dict[str, Any],
    rp_id: str
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Generate authentication options for navigator.credentials.get().
    Returns (success, error_message, options_dict).
    """
    creds = db.execute(
        "SELECT credential_id FROM webauthn_credentials WHERE emp_id = ?",
        (user["id"],)
    ).fetchall()

    if not creds:
        return False, "No biometric device registered for this employee. Please register your phone first.", None

    challenge_bytes = secrets.token_bytes(32)
    challenge_b64 = b64url_encode(challenge_bytes)

    allow_list = [{"type": "public-key", "id": row["credential_id"], "transports": ["internal"]} for row in creds]

    options = {
        "challenge": challenge_b64,
        "rpId": rp_id,
        "allowCredentials": allow_list,
        "userVerification": "required",  # Strictly require Face ID / biometric
        "timeout": 60000,
    }
    return True, None, options


def verify_authentication_response(
    db,
    user: Dict[str, Any],
    payload: Dict[str, Any],
    stored_challenge: str,
    expected_rp_id: str
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Cryptographically verify client credentials.get() assertion (Face ID punch).
    Returns (success, message, cred_record).
    """
    try:
        raw_id_b64 = payload.get("id") or payload.get("rawId")
        if not raw_id_b64:
            return False, "Missing credential identifier in biometric assertion.", None

        resp = payload.get("response") or {}
        client_data_raw = b64url_decode(resp.get("clientDataJSON", ""))
        auth_data_raw = b64url_decode(resp.get("authenticatorData", ""))
        signature_raw = b64url_decode(resp.get("signature", ""))

        client_data = json.loads(client_data_raw.decode("utf-8"))
        if client_data.get("type") != "webauthn.get":
            return False, "Invalid biometric client request type.", None

        if client_data.get("challenge") != stored_challenge:
            return False, "Biometric challenge has expired. Please tap verify again.", None

        # -------------------------------------------------------------------------
        # Cross-Account Buddy Punch Check:
        # -------------------------------------------------------------------------
        cred_row = db.execute(
            "SELECT * FROM webauthn_credentials WHERE credential_id = ?",
            (raw_id_b64,)
        ).fetchone()

        if not cred_row:
            return False, "Unrecognized device credential. Please use your registered phone.", None

        if cred_row["emp_id"] != user["id"]:
            # Cross-account breach: another employee's phone was used
            other_emp = db.execute("SELECT id, emp_code, name FROM employees WHERE id = ?", (cred_row["emp_id"],)).fetchone()
            try:
                from .security_shield import block_employee_account_by_ai
                other_name = other_emp["name"] if other_emp else "other employee"
                other_code = other_emp["emp_code"] if other_emp else "other"
                block_msg = (
                    f"AI Buddy Punch Violation: Biometric hardware signature belongs to {other_name} ({other_code}). "
                    f"Account locked pending HR permission."
                )
                block_employee_account_by_ai(
                    db,
                    user["id"],
                    reason=block_msg,
                    target_emp_id=cred_row["emp_id"],
                    similarity=1.0,
                )
            except Exception as block_err:
                pass
            return False, "Biometric authentication failed: Phone is registered to another employee. Account locked.", None

        # Check authenticatorData
        if len(auth_data_raw) < 37:
            return False, "Invalid biometric authenticator data.", None

        rp_id_hash = auth_data_raw[:32]
        expected_rp_hash = hashlib.sha256(expected_rp_id.encode("utf-8")).digest()
        # In non-strict dev environments or proxies, allow host match
        if rp_id_hash != expected_rp_hash and expected_rp_id not in ("localhost", "127.0.0.1"):
            return False, "Relying party ID mismatch.", None

        flags = auth_data_raw[32]
        user_present = bool(flags & 0x01)
        user_verified = bool(flags & 0x04)

        if not user_present:
            return False, "User presence check failed.", None

        # Strict requirement: userVerification MUST be true (biometric Face ID confirmed)
        if not user_verified:
            return False, "Biometric Face ID authentication is strictly required for punching.", None

        sign_count = int.from_bytes(auth_data_raw[33:37], "big")

        # Cryptographic Signature Verification
        client_data_hash = hashlib.sha256(client_data_raw).digest()
        verification_data = auth_data_raw + client_data_hash

        pub_key_pem = cred_row["public_key"].encode("utf-8")
        pub_key = serialization.load_pem_public_key(pub_key_pem)

        if isinstance(pub_key, ec.EllipticCurvePublicKey):
            pub_key.verify(signature_raw, verification_data, ec.ECDSA(hashes.SHA256()))
        elif isinstance(pub_key, rsa.RSAPublicKey):
            pub_key.verify(signature_raw, verification_data, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False, "Unsupported key type for verification.", None

        # Update signature count
        db.execute(
            "UPDATE webauthn_credentials SET sign_count = ? WHERE id = ?",
            (sign_count, cred_row["id"])
        )
        db.commit()

        return True, "Phone Face ID verified successfully.", dict(cred_row)

    except Exception as e:
        return False, f"Biometric signature error: {str(e)}", None


def reset_device_for_employee(db, emp_id: int, reset_by_id: Optional[int] = None) -> bool:
    """HR-assisted process to reset or replace an employee's registered phone."""
    db.execute("DELETE FROM webauthn_credentials WHERE emp_id = ?", (emp_id,))
    db.execute(
        "UPDATE employees SET device_registered=0, device_name=NULL, device_registered_at=NULL, "
        "face_registered=0, face_template=NULL, face_image=NULL, face_registered_at=NULL, face_registered_by=NULL "
        "WHERE id = ?",
        (emp_id,)
    )
    actor_info = f"reset_by_hr={reset_by_id}" if reset_by_id else "self_reset"
    audit("WEBAUTHN_DEVICE_RESET", "webauthn", emp_id, actor_info)
    db.commit()
    return True
