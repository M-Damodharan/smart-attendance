import os
import zipfile

PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
ZIP_OUTPUT = os.path.join(PROJECT_DIR, "smart-attendance-final.zip")

EXCLUDE_DIRS = {
    ".git",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    "scratch",
    ".gradle",
    "build",
}

EXCLUDE_FILES = {
    ".env",
    "smart-attendance-final.zip",
    "test_final.db",
    ".turso_token",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
}


def make_final_zip():
    print(f"Creating clean production ZIP archive: {ZIP_OUTPUT}...")
    file_count = 0
    total_size = 0

    with zipfile.ZipFile(ZIP_OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PROJECT_DIR):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]

            for file in files:
                if file in EXCLUDE_FILES:
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext in EXCLUDE_EXTENSIONS:
                    continue

                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PROJECT_DIR)

                # Skip any nested build or venv files
                parts = rel_path.split(os.sep)
                if any(p in EXCLUDE_DIRS for p in parts):
                    continue

                zf.write(full_path, rel_path)
                file_count += 1
                total_size += os.path.getsize(full_path)

    mb = round(total_size / (1024 * 1024), 2)
    zip_mb = round(os.path.getsize(ZIP_OUTPUT) / (1024 * 1024), 2)
    print(f"SUCCESS: Packaged {file_count} files ({mb} MB uncompressed, {zip_mb} MB compressed) into {ZIP_OUTPUT}")


if __name__ == "__main__":
    make_final_zip()
