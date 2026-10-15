import os
import zipfile

def create_zip(source_dir, output_zip):
    exclude_dirs = {'.git', 'venv', '__pycache__', '.pytest_cache', 'build', '.gradle'}
    exclude_exts = {'.pyc', '.log'}
    
    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                if any(file.endswith(ext) for ext in exclude_exts):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, source_dir)
                zipf.write(file_path, rel_path)
    print(f"Zip created successfully at {output_zip} (Size: {os.path.getsize(output_zip):,} bytes)")

if __name__ == "__main__":
    create_zip("d:/smart-attendance", "d:/smart-attendance-sri-balaji-gears.zip")
