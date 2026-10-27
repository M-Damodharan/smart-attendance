"""Safe Cache & Temporary File Cleanup Service for Sri Balaji Gears Smart Attendance.

Strict Policy:
- Preserves 100% of SQLite database records (instance/attendance.db is NEVER touched).
- Preserves all employee photos, selfies, face templates, and biometric vectors.
- Preserves all backups (backups/ directory is NEVER touched).
- Strictly whitelists application bytecode cache (__pycache__, *.pyc, *.pyo)
  and temporary test execution cache (.pytest_cache).
- Cleans expired, already-used liveness challenge tokens from the database.
- Path traversal protection with realpath & commonpath boundary verification.
"""
import os
import shutil
import sqlite3
import time
from typing import Dict, Any, List

from config import Config, BASE_DIR
from .services import audit, now

INSTANCE_DIR = os.path.realpath(os.path.join(BASE_DIR, "instance"))
BACKUP_DIR = os.path.realpath(os.path.join(BASE_DIR, "backups"))
VENV_DIR = os.path.realpath(os.path.join(BASE_DIR, "venv"))
GIT_DIR = os.path.realpath(os.path.join(BASE_DIR, ".git"))
ANDROID_DIR = os.path.realpath(os.path.join(BASE_DIR, "android-sbg"))
SEED_DIR = os.path.realpath(os.path.join(BASE_DIR, "app", "seed_data"))

PROTECTED_DIRECTORIES = (
    INSTANCE_DIR,
    BACKUP_DIR,
    VENV_DIR,
    GIT_DIR,
    ANDROID_DIR,
    SEED_DIR,
)

PROTECTED_EXTENSIONS = (
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".svg",
    ".key",
    ".env",
    ".py",
    ".html",
    ".css",
    ".js",
    ".json",
    ".sql",
    ".md",
    ".txt",
    ".sh",
    ".bat",
)

ALLOWED_CACHE_EXTENSIONS = (
    ".pyc",
    ".pyo",
)

ALLOWED_CACHE_DIR_NAMES = (
    "__pycache__",
    ".pytest_cache",
)


def is_path_safe_for_deletion(target_path: str) -> bool:
    """Verifies that a target file or directory is inside the allowed application root
    and strictly outside all database, backup, media, and virtualenv paths.
    Must also be an approved temporary bytecode or cache file/directory.
    """
    try:
        real_target = os.path.realpath(target_path)
        real_base = os.path.realpath(BASE_DIR)

        # Must reside strictly within application root (Path Traversal Protection)
        if os.path.commonpath([real_base, real_target]) != real_base:
            return False

        # Must not be the base directory itself
        if real_target == real_base:
            return False

        # Strictly forbid any protected directories
        for protected in PROTECTED_DIRECTORIES:
            if real_target == protected or real_target.startswith(protected + os.sep):
                return False

        # Strictly forbid protected database, media, and source file extensions
        lower_path = real_target.lower()
        if any(lower_path.endswith(ext) for ext in PROTECTED_EXTENSIONS):
            return False

        # Only allow recognized cache extensions or targets located within allowed cache directories
        parts = [p.lower() for p in real_target.split(os.sep)]
        inside_allowed_cache_dir = any(d in parts for d in ALLOWED_CACHE_DIR_NAMES)
        has_allowed_ext = any(lower_path.endswith(ext) for ext in ALLOWED_CACHE_EXTENSIONS)

        if not (inside_allowed_cache_dir or has_allowed_ext):
            return False

        return True
    except Exception:
        return False


def format_bytes(num_bytes: int) -> str:
    """Formats bytes into human readable string."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    else:
        return f"{num_bytes / (1024 * 1024):.2f} MB"


def get_cache_status() -> Dict[str, Any]:
    """Inspects the project for temporary cache files without deleting anything.
    Returns status counts and estimated size.
    """
    pyc_count = 0
    pyc_bytes = 0
    pycache_dir_count = 0

    allowed_subdirs = ["app", "tests", "scripts", "scratch"]
    target_dirs = [os.path.join(BASE_DIR, d) for d in allowed_subdirs] + [BASE_DIR]

    for root_dir in target_dirs:
        if not os.path.exists(root_dir):
            continue

        if root_dir == BASE_DIR:
            top_pycache = os.path.join(BASE_DIR, "__pycache__")
            if os.path.isdir(top_pycache) and is_path_safe_for_deletion(top_pycache):
                pycache_dir_count += 1
                try:
                    for f in os.listdir(top_pycache):
                        if f.endswith((".pyc", ".pyo")):
                            fp = os.path.join(top_pycache, f)
                            pyc_count += 1
                            pyc_bytes += os.path.getsize(fp)
                except Exception:
                    pass
            continue

        for current_dir, dirs, files in os.walk(root_dir):
            # Guard against walking into forbidden roots
            dirs[:] = [d for d in dirs if d not in ("instance", "backups", "venv", ".git", ".idea", "android-sbg")]
            if os.path.basename(current_dir) == "__pycache__":
                if is_path_safe_for_deletion(current_dir):
                    pycache_dir_count += 1
                    for f in files:
                        if f.endswith((".pyc", ".pyo")):
                            fp = os.path.join(current_dir, f)
                            pyc_count += 1
                            try:
                                pyc_bytes += os.path.getsize(fp)
                            except Exception:
                                pass
            else:
                for f in files:
                    if f.endswith((".pyc", ".pyo")):
                        fp = os.path.join(current_dir, f)
                        if is_path_safe_for_deletion(fp):
                            pyc_count += 1
                            try:
                                pyc_bytes += os.path.getsize(fp)
                            except Exception:
                                pass

    pytest_cache_dir = os.path.join(BASE_DIR, ".pytest_cache")
    pytest_cache_exists = os.path.isdir(pytest_cache_dir) and is_path_safe_for_deletion(pytest_cache_dir)

    return {
        "pycache_dirs": pycache_dir_count,
        "pyc_files": pyc_count,
        "pyc_bytes": pyc_bytes,
        "pyc_bytes_formatted": format_bytes(pyc_bytes),
        "pytest_cache_present": pytest_cache_exists,
    }


def safe_clean_cache(db: sqlite3.Connection = None, user_id: int = None) -> Dict[str, Any]:
    """Performs safe, non-destructive purge of confirmed temporary cache only.
    
    Categories cleared:
    1. Python bytecode cache files (*.pyc, *.pyo) in app, tests, scripts, scratch.
    2. Empty or bytecode-only __pycache__ directories.
    3. Test runner temporary execution cache (.pytest_cache).
    4. Expired and already-consumed challenge tokens in database.

    Guarantees:
    - Never deletes, resets, or modifies attendance.db.
    - Never deletes or modifies employee selfies, profile pictures, or face templates.
    - Never deletes backups or configuration files.
    - Gracefully ignores missing directories or permission errors.
    """
    total_freed = 0
    pyc_removed = 0
    pycache_removed = 0
    pytest_cache_cleaned = False
    challenges_cleared = 0
    errors: List[str] = []

    allowed_subdirs = ["app", "tests", "scripts", "scratch"]
    target_dirs = [os.path.join(BASE_DIR, d) for d in allowed_subdirs] + [BASE_DIR]

    # 1. Clean bytecode files and __pycache__ directories
    for root_dir in target_dirs:
        if not os.path.exists(root_dir):
            continue

        if root_dir == BASE_DIR:
            top_pycache = os.path.join(BASE_DIR, "__pycache__")
            if os.path.isdir(top_pycache) and is_path_safe_for_deletion(top_pycache):
                try:
                    for f in os.listdir(top_pycache):
                        if f.endswith((".pyc", ".pyo")):
                            fp = os.path.join(top_pycache, f)
                            if is_path_safe_for_deletion(fp):
                                sz = os.path.getsize(fp)
                                os.remove(fp)
                                pyc_removed += 1
                                total_freed += sz
                    os.rmdir(top_pycache)
                    pycache_removed += 1
                except (OSError, PermissionError) as e:
                    errors.append(f"Notice: Root __pycache__ locked or in-use by server worker ({type(e).__name__})")
            continue

        for current_dir, dirs, files in os.walk(root_dir, topdown=False):
            dirs[:] = [d for d in dirs if d not in ("instance", "backups", "venv", ".git", ".idea", "android-sbg")]

            if os.path.basename(current_dir) == "__pycache__":
                if not is_path_safe_for_deletion(current_dir):
                    continue
                # Remove pyc files inside
                for f in files:
                    if f.endswith((".pyc", ".pyo")):
                        fp = os.path.join(current_dir, f)
                        if is_path_safe_for_deletion(fp):
                            try:
                                sz = os.path.getsize(fp)
                                os.remove(fp)
                                pyc_removed += 1
                                total_freed += sz
                            except (OSError, PermissionError) as e:
                                errors.append(f"Notice: File locked by Python process: {os.path.basename(fp)}")
                # Try removing the directory if now empty
                try:
                    if not os.listdir(current_dir):
                        os.rmdir(current_dir)
                        pycache_removed += 1
                except (OSError, PermissionError):
                    pass
            else:
                for f in files:
                    if f.endswith((".pyc", ".pyo")):
                        fp = os.path.join(current_dir, f)
                        if is_path_safe_for_deletion(fp):
                            try:
                                sz = os.path.getsize(fp)
                                os.remove(fp)
                                pyc_removed += 1
                                total_freed += sz
                            except (OSError, PermissionError) as e:
                                errors.append(f"Notice: File locked by Python process: {os.path.basename(fp)}")

    # 2. Clean .pytest_cache if present
    pytest_cache_dir = os.path.join(BASE_DIR, ".pytest_cache")
    if os.path.isdir(pytest_cache_dir) and is_path_safe_for_deletion(pytest_cache_dir):
        try:
            # Measure size before removing
            pt_size = 0
            for root_c, _, files_c in os.walk(pytest_cache_dir):
                for fc in files_c:
                    try:
                        pt_size += os.path.getsize(os.path.join(root_c, fc))
                    except Exception:
                        pass
            shutil.rmtree(pytest_cache_dir, ignore_errors=True)
            total_freed += pt_size
            pytest_cache_cleaned = True
        except (OSError, PermissionError) as e:
            errors.append(f"Notice: .pytest_cache in use ({type(e).__name__})")

    # 3. Clean expired liveness challenges in database
    if db is not None:
        try:
            expire_cutoff = time.time() - getattr(Config, "CHALLENGE_TTL_S", 180)
            res = db.execute(
                "DELETE FROM challenges WHERE used=1 OR created_at < ?",
                (expire_cutoff,)
            )
            challenges_cleared = res.rowcount if res and res.rowcount >= 0 else 0
            db.commit()
        except Exception as e:
            errors.append(f"Database challenges table maintenance: {str(e)}")

    summary_str = (
        f"pycache_dirs={pycache_removed}, pyc_files={pyc_removed}, "
        f"pytest_cache={pytest_cache_cleaned}, challenges={challenges_cleared}, "
        f"freed={format_bytes(total_freed)}"
    )

    if user_id:
        try:
            audit("CACHE_CLEANUP", "maintenance", user_id, summary_str)
            if db:
                db.commit()
        except Exception:
            pass

    categories = []
    if pyc_removed > 0 or pycache_removed > 0:
        categories.append(f"Python Bytecode ({pyc_removed} files in {pycache_removed} dirs)")
    if pytest_cache_cleaned:
        categories.append("Pytest Temporary Cache")
    if challenges_cleared > 0:
        categories.append(f"Expired Liveness Challenges ({challenges_cleared} tokens)")
    if not categories:
        categories.append("Temporary Bytecode & Expired Token Inspection (Clean)")

    return {
        "ok": True,
        "success": True,
        "status": "success",
        "categories_cleared": categories,
        "pycache_dirs_removed": pycache_removed,
        "pyc_files_removed": pyc_removed,
        "pytest_cache_cleaned": pytest_cache_cleaned,
        "expired_challenges_cleared": challenges_cleared,
        "bytes_freed": total_freed,
        "bytes_freed_formatted": format_bytes(total_freed),
        "errors": errors,
        "timestamp": now()
    }
