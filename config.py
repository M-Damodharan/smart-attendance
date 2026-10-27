import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _load_env_file():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass


_load_env_file()

ENV = os.environ.get("APP_ENV", "production" if os.environ.get("RENDER") else "development")
PRODUCTION = (ENV == "production") or bool(os.environ.get("RENDER"))


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


DEFAULT_TURSO_TOKEN = (
    "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9."
    "eyJhIjoicnciLCJpYXQiOjE3OTMwODYwOTcsImlkIjoiMDFhMTdjNDItNDEwMS03ZjA0LWE2OWUtODgyYTgyYjRiYTQ3Iiwia2lkIjoiOE5CcW9uNmNDMmZHR1ZMM2NLZ3FNd2pqZzd6NWJOaWFUTGRaei0tTlZXWSIsInJpZCI6IjI3OGU4NGU1LTliNmMtNDU5NC1hYjJkLTcxM2VlYjU5ZjZmZSJ9."
    "dplC14DykiOUJ12lI07b6owuv3pdxGmyMRK_nedKJbSGXzcjePrXJtT6N-8c2U1yO7b3hcNclZ569nbkiMkiAA"
)


def _resolve_turso_token():
    tok = os.environ.get("TURSO_AUTH_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    token_file = os.path.join(BASE_DIR, "instance", ".turso_token")
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                t = f.read().strip()
                if t:
                    return t
        except Exception:
            pass
    return DEFAULT_TURSO_TOKEN


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

    # Permanent 24/7 Cloud Database (Turso LibSQL in AWS Mumbai)
    TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "sbg-attendance-m-damodharan.aws-ap-south-1.turso.io")
    TURSO_AUTH_TOKEN = _resolve_turso_token()
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if DATABASE_URL and (DATABASE_URL.startswith("libsql://") or "turso.io" in DATABASE_URL):
        TURSO_DATABASE_URL = DATABASE_URL

    # Prevent silent fallback to local SQLite in production:
    if PRODUCTION and not (os.environ.get("TESTING") == "1"):
        if not (TURSO_DATABASE_URL and TURSO_AUTH_TOKEN):
            raise RuntimeError(
                "CRITICAL PRODUCTION CONFIG ERROR: TURSO_DATABASE_URL and TURSO_AUTH_TOKEN "
                "must be provided in Render environment variables. Silent fallback to local SQLite "
                "is strictly prohibited in production to prevent data loss across restarts."
            )

    # Cloud persistence is active whenever Turso cloud credentials are configured
    USE_TURSO = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN) and not (os.environ.get("TESTING") == "1")



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
