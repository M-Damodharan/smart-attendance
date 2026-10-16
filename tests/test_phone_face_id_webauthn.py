import base64
import hashlib
import io
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import secrets
import pytest
import cbor2
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization

from app import create_app
from app.db import get_db
from app.webauthn_engine import b64url_encode, b64url_decode, reset_device_for_employee


def helper_generate_phone_key_and_attestation(rp_id="localhost"):
    """Helper to simulate a phone's Secure Enclave generating an ECDSA key pair and attestation object."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()
    pub_nums = pub.public_numbers()
    x_bytes = pub_nums.x.to_bytes(32, "big")
    y_bytes = pub_nums.y.to_bytes(32, "big")

    cose_key = {
        1: 2,   # EC2
        3: -7,  # ES256
        -1: 1,  # P-256
        -2: x_bytes,
        -3: y_bytes
    }

    rp_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
    flags = bytes([0x45])  # UP (0x01) | UV (0x04) | AT (0x40)
    sign_count = (0).to_bytes(4, "big")
    aaguid = os.urandom(16)
    cred_id = os.urandom(32)
    cred_id_len = len(cred_id).to_bytes(2, "big")

    auth_data = rp_hash + flags + sign_count + aaguid + cred_id_len + cred_id + cbor2.dumps(cose_key)
    att_obj = {"fmt": "none", "attStmt": {}, "authData": auth_data}
    att_obj_raw = cbor2.dumps(att_obj)

    return priv, cred_id, att_obj_raw


@pytest.fixture
def app_instance(tmp_path):
    db_file = tmp_path / "test_webauthn.db"
    selfie_dir = tmp_path / "selfies"
    selfie_dir.mkdir(parents=True, exist_ok=True)
    app = create_app({
        "TESTING": True,
        "DATABASE": str(db_file),
        "SELFIE_DIR": str(selfie_dir),
        "SECRET_KEY": "test-key-webauthn",
        "DEMO_DATA": False,
        "WTF_CSRF_ENABLED": False,
        "MAX_GPS_ACCURACY_M": 500,
        "GEOFENCE_TOLERANCE_M": 100,
    })
    return app


@pytest.fixture
def client(app_instance):
    return app_instance.test_client()


def test_webauthn_challenge_flow(app_instance, client):
    """Test registration and authentication challenge generation."""
    with client.session_transaction() as sess:
        with app_instance.app_context():
            db = get_db()
            emp = db.execute("SELECT * FROM employees WHERE emp_code='E0012'").fetchone()
            sess["uid"] = emp["id"]
            sess["role"] = emp["role"]
            sess["login_type"] = "staff"

    # 1. Registration Challenge
    res = client.post("/api/webauthn/register/challenge")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "options" in data
    assert "challenge" in data["options"]
    assert data["options"]["authenticatorSelection"]["userVerification"] == "required"


def test_device_enrollment_and_multi_account_prevention(app_instance, client):
    """Test device enrollment with hardware Face ID and AI defense against using the same phone for 2 accounts."""
    with app_instance.app_context():
        db = get_db()
        emp1 = db.execute("SELECT * FROM employees WHERE emp_code='E0012'").fetchone()
        emp2 = db.execute("SELECT * FROM employees WHERE emp_code='EMP001'").fetchone()

    # Step 1: Employee 1 logs in and requests registration challenge
    with client.session_transaction() as sess:
        sess["uid"] = emp1["id"]
        sess["role"] = emp1["role"]
        sess["login_type"] = "staff"

    res = client.post("/api/webauthn/register/challenge")
    reg_opts = res.get_json()["options"]
    challenge_b64 = reg_opts["challenge"]

    # Simulate Phone 1 Face ID creation
    priv1, cred_id1, att_obj1 = helper_generate_phone_key_and_attestation(rp_id="localhost")
    client_data1 = json.dumps({"type": "webauthn.create", "challenge": challenge_b64, "origin": "http://localhost"}).encode("utf-8")

    reg_payload1 = {
        "id": b64url_encode(cred_id1),
        "rawId": b64url_encode(cred_id1),
        "type": "public-key",
        "response": {
            "clientDataJSON": b64url_encode(client_data1),
            "attestationObject": b64url_encode(att_obj1)
        },
        "device_name": "iPhone 15 Pro (Damodharan Face ID)"
    }

    # Step 2: Submit registration
    reg_res1 = client.post("/api/webauthn/register/verify", json=reg_payload1)
    assert reg_res1.status_code == 200
    assert reg_res1.get_json()["ok"] is True

    # Verify database state for Employee 1
    with app_instance.app_context():
        db = get_db()
        emp1_row = db.execute("SELECT * FROM employees WHERE id=?", (emp1["id"],)).fetchone()
        assert emp1_row["device_registered"] == 1
        assert "iPhone 15 Pro" in emp1_row["device_name"]
        cred_row = db.execute("SELECT * FROM webauthn_credentials WHERE emp_id=?", (emp1["id"],)).fetchone()
        assert cred_row is not None
        assert cred_row["credential_id"] == b64url_encode(cred_id1)

    # Step 3: Employee 2 tries to register the EXACT SAME phone/credential ID (Buddy punch attempt)
    with client.session_transaction() as sess:
        sess["uid"] = emp2["id"]
        sess["role"] = emp2["role"]
        sess["login_type"] = "employee"

    res2 = client.post("/api/webauthn/register/challenge")
    challenge2_b64 = res2.get_json()["options"]["challenge"]

    client_data2 = json.dumps({"type": "webauthn.create", "challenge": challenge2_b64, "origin": "http://localhost"}).encode("utf-8")
    reg_payload2 = {
        "id": b64url_encode(cred_id1),  # SAME credential ID from phone 1
        "rawId": b64url_encode(cred_id1),
        "type": "public-key",
        "response": {
            "clientDataJSON": b64url_encode(client_data2),
            "attestationObject": b64url_encode(att_obj1)
        },
        "device_name": "Buddy Punch Phone"
    }

    reg_res2 = client.post("/api/webauthn/register/verify", json=reg_payload2)
    assert reg_res2.status_code == 400
    assert "already registered to another employee" in reg_res2.get_json()["error"]

    # Verify AI Shield auto-blocked the offending account
    with app_instance.app_context():
        db = get_db()
        emp2_row = db.execute("SELECT * FROM employees WHERE id=?", (emp2["id"],)).fetchone()
        assert emp2_row["active"] == 0
        assert "AI Multi-Account Violation" in (emp2_row["block_reason"] or "")


def test_biometric_face_id_punch_success(app_instance, client):
    """Test full attendance punch authenticated by Phone Face ID cryptographic signature."""
    with app_instance.app_context():
        db = get_db()
        emp = db.execute("SELECT * FROM employees WHERE emp_code='E0012'").fetchone()

    with client.session_transaction() as sess:
        sess["uid"] = emp["id"]
        sess["role"] = emp["role"]
        sess["login_type"] = "staff"

    # 1. Register device
    reg_res = client.post("/api/webauthn/register/challenge")
    ch_b64 = reg_res.get_json()["options"]["challenge"]
    priv, cred_id, att_obj = helper_generate_phone_key_and_attestation(rp_id="localhost")
    cdata = json.dumps({"type": "webauthn.create", "challenge": ch_b64, "origin": "http://localhost"}).encode("utf-8")

    client.post("/api/webauthn/register/verify", json={
        "id": b64url_encode(cred_id),
        "rawId": b64url_encode(cred_id),
        "type": "public-key",
        "response": {"clientDataJSON": b64url_encode(cdata), "attestationObject": b64url_encode(att_obj)},
        "device_name": "iPhone 15 Face ID"
    })

    # 2. Punch Check-In: Request punch challenge
    punch_ch_res = client.post("/api/webauthn/punch/challenge")
    assert punch_ch_res.status_code == 200
    punch_opts = punch_ch_res.get_json()["options"]
    punch_challenge_b64 = punch_opts["challenge"]

    # 3. Phone signs challenge with private key
    rp_hash = hashlib.sha256(b"localhost").digest()
    auth_flags = bytes([0x05])  # UP (0x01) | UV (0x04 - Biometric Face ID Verified)
    sign_count = (1).to_bytes(4, "big")
    auth_data = rp_hash + auth_flags + sign_count

    auth_client_data = json.dumps({"type": "webauthn.get", "challenge": punch_challenge_b64, "origin": "http://localhost"}).encode("utf-8")
    auth_client_hash = hashlib.sha256(auth_client_data).digest()
    verify_data = auth_data + auth_client_hash

    signature = priv.sign(verify_data, ec.ECDSA(hashes.SHA256()))

    assertion_payload = {
        "id": b64url_encode(cred_id),
        "rawId": b64url_encode(cred_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": b64url_encode(auth_client_data),
            "authenticatorData": b64url_encode(auth_data),
            "signature": b64url_encode(signature),
        }
    }

    # Dummy camera selfie snapshot (for attendance audit record)
    dummy_selfie = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x08\x00\x08\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9").decode()

    punch_res = client.post("/api/punch", json={
        "kind": "in",
        "lat": 0, "lng": 0, "accuracy": 10,
        "selfie": dummy_selfie,
        "webauthn_assertion": assertion_payload
    })

    assert punch_res.status_code == 200
    punch_data = punch_res.get_json()
    assert punch_data["ok"] is True
    assert punch_data["auth_method"] == "webauthn_face_id"
    assert punch_data["face_verified"] is True

    # Verify attendance DB record
    with app_instance.app_context():
        db = get_db()
        att = db.execute("SELECT * FROM attendance WHERE emp_id=?", (emp["id"],)).fetchone()
        assert att is not None
        assert att["auth_method"] == "webauthn_face_id"
        assert att["face_verified"] == 1
        assert att["device_credential_id"] == b64url_encode(cred_id)


def test_tampered_signature_strictly_rejected(app_instance, client):
    """Test that forged or tampered biometric signatures are strictly rejected."""
    with app_instance.app_context():
        db = get_db()
        emp = db.execute("SELECT * FROM employees WHERE emp_code='E0012'").fetchone()

    with client.session_transaction() as sess:
        sess["uid"] = emp["id"]
        sess["role"] = emp["role"]
        sess["login_type"] = "staff"

    # Register
    reg_res = client.post("/api/webauthn/register/challenge")
    ch_b64 = reg_res.get_json()["options"]["challenge"]
    priv, cred_id, att_obj = helper_generate_phone_key_and_attestation(rp_id="localhost")
    cdata = json.dumps({"type": "webauthn.create", "challenge": ch_b64, "origin": "http://localhost"}).encode("utf-8")
    client.post("/api/webauthn/register/verify", json={
        "id": b64url_encode(cred_id),
        "rawId": b64url_encode(cred_id),
        "type": "public-key",
        "response": {"clientDataJSON": b64url_encode(cdata), "attestationObject": b64url_encode(att_obj)}
    })

    # Punch with invalid signature
    punch_ch_res = client.post("/api/webauthn/punch/challenge")
    punch_challenge_b64 = punch_ch_res.get_json()["options"]["challenge"]

    rp_hash = hashlib.sha256(b"localhost").digest()
    auth_flags = bytes([0x05])
    sign_count = (1).to_bytes(4, "big")
    auth_data = rp_hash + auth_flags + sign_count
    auth_client_data = json.dumps({"type": "webauthn.get", "challenge": punch_challenge_b64, "origin": "http://localhost"}).encode("utf-8")

    bogus_signature = b"corrupted_invalid_signature_bytes_123456"

    assertion_payload = {
        "id": b64url_encode(cred_id),
        "rawId": b64url_encode(cred_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": b64url_encode(auth_client_data),
            "authenticatorData": b64url_encode(auth_data),
            "signature": b64url_encode(bogus_signature),
        }
    }

    dummy_selfie = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x08\x00\x08\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9").decode()

    punch_res = client.post("/api/punch", json={
        "kind": "in",
        "lat": 0, "lng": 0, "accuracy": 10,
        "selfie": dummy_selfie,
        "webauthn_assertion": assertion_payload
    })

    assert punch_res.status_code == 403
    assert "error" in punch_res.get_json()


def test_hr_device_reset_flow(app_instance, client):
    """Test HR assisted phone replacement / device reset workflow."""
    with app_instance.app_context():
        db = get_db()
        emp = db.execute("SELECT * FROM employees WHERE emp_code='E0012'").fetchone()
        hr = db.execute("SELECT * FROM employees WHERE role='hr'").fetchone()

        # Enroll device first
        _, cred_id, _ = helper_generate_phone_key_and_attestation()
        db.execute("INSERT INTO webauthn_credentials (emp_id, credential_id, public_key) VALUES (?,?,?)",
                   (emp["id"], b64url_encode(cred_id), "DUMMY_KEY"))
        db.execute("UPDATE employees SET device_registered=1, device_name='Old iPhone' WHERE id=?", (emp["id"],))
        db.commit()

        # Verify enrolled
        row = db.execute("SELECT device_registered FROM employees WHERE id=?", (emp["id"],)).fetchone()
        assert row["device_registered"] == 1

    # HR logs in and resets device
    with client.session_transaction() as sess:
        sess["uid"] = hr["id"]
        sess["role"] = "hr"
        sess["login_type"] = "staff"

    reset_res = client.post(f"/hr/employees/{emp['id']}/reset-device", follow_redirects=True)
    assert reset_res.status_code == 200

    # Verify device credentials deleted and flag reset
    with app_instance.app_context():
        db = get_db()
        row = db.execute("SELECT device_registered, device_name FROM employees WHERE id=?", (emp["id"],)).fetchone()
        assert row["device_registered"] == 0
        assert row["device_name"] is None
        creds = db.execute("SELECT COUNT(*) FROM webauthn_credentials WHERE emp_id=?", (emp["id"],)).fetchone()[0]
        assert creds == 0
