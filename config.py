import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


ENV = os.environ.get("APP_ENV", "development")
PRODUCTION = ENV == "production"


def _resolve_secret_key():
    key = os.environ.get("SECRET_KEY")
    if key and key not in ("", "change-this-secret-in-production"):
        return key
    key_file = os.path.join(BASE_DIR, "instance", ".secret_key")
    try:
        if os.path.exists(key_file):
            with open(key_file, "r") as f:
                k = f.read().strip()
                if k:
                    return k
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        new_key = secrets.token_hex(32)
        with open(key_file, "w") as f:
            f.write(new_key)
        return new_key
    except Exception:
        return secrets.token_hex(32)


class Config:
    ENV_NAME = ENV
    PRODUCTION = PRODUCTION
    DEMO_DATA = os.environ.get("DEMO_DATA", "0" if PRODUCTION else "1") == "1"
    PERMANENT_SESSION_LIFETIME = 30 * 24 * 3600     # persistent login for 30 days
    BEHIND_PROXY = os.environ.get("BEHIND_PROXY", "1" if PRODUCTION else "0") == "1"
    LOG_FILE = os.environ.get("LOG_FILE", os.path.join(BASE_DIR, "instance", "app.log"))

    # ---- Security -------------------------------------------------------
    SECRET_KEY = _resolve_secret_key()
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Set SESSION_COOKIE_SECURE=1 when served over HTTPS
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1" if PRODUCTION else "0") == "1"
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB per request

    # ---- Storage --------------------------------------------------------
    DATABASE = os.environ.get("DATABASE", os.path.join(BASE_DIR, "instance", "attendance.db"))
    SELFIE_DIR = os.environ.get("SELFIE_DIR", os.path.join(BASE_DIR, "instance", "selfies"))
    TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "sbg-attendance-m-damodharan.aws-ap-south-1.turso.io" if PRODUCTION else None)
    TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")
    if not TURSO_AUTH_TOKEN:
        token_file = os.path.join(BASE_DIR, "instance", ".turso_token")
        if os.path.exists(token_file):
            try:
                with open(token_file, "r") as f:
                    TURSO_AUTH_TOKEN = f.read().strip()
            except Exception:
                pass
    USE_TURSO = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)


    # ---- Attendance rules -----------------------------------------------
    WEEKLY_OFF = (6,)            # Monday=0 ... Sunday=6
    MAX_GPS_ACCURACY_M = int(os.environ.get("MAX_GPS_ACCURACY_M", 350))     # reject fixes less accurate than this
    GEOFENCE_TOLERANCE_M = int(os.environ.get("GEOFENCE_TOLERANCE_M", 50))     # extra metres allowed on top of office radius
    CHALLENGE_TTL_S = 180        # liveness challenge validity
    MIN_PUNCH_GAP_MIN = 1        # minimum minutes between check-in and check-out
    MIN_MOTION_SCORE = 1.2       # server-side liveness: min avg frame difference (0-255)
    REQUIRE_LIVENESS_MOTION = True

    # ---- Login throttling -----------------------------------------------
    MAX_LOGIN_ATTEMPTS = 5
    LOGIN_LOCK_SECONDS = 600
