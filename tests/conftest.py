import os
import pytest
from app.auth import clear_user_cache
from app.services import clear_shared_cache
from app.turso_db import clear_turso_cache

ORIGINAL_DATABASE = os.environ.get("DATABASE")
ORIGINAL_SELFIE_DIR = os.environ.get("SELFIE_DIR")

@pytest.fixture(autouse=True)
def clean_test_isolation_state():
    """Ensure in-memory caches and cross-test state are completely reset between test runs."""
    clear_user_cache()
    clear_shared_cache()
    clear_turso_cache()
    yield
    clear_user_cache()
    clear_shared_cache()
    clear_turso_cache()
    if ORIGINAL_DATABASE is not None:
        os.environ["DATABASE"] = ORIGINAL_DATABASE
    else:
        os.environ.pop("DATABASE", None)
    if ORIGINAL_SELFIE_DIR is not None:
        os.environ["SELFIE_DIR"] = ORIGINAL_SELFIE_DIR
    else:
        os.environ.pop("SELFIE_DIR", None)
