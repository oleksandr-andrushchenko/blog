import os
import shutil
import subprocess
from pathlib import Path

# Directories
root_dir = Path(__file__).parent.parent
output_dir = root_dir / ".code-build"
be_function_dir = root_dir / "be-function-src"
tmp_dir = output_dir / "tmp_be-function"
zip_file = output_dir / "be-function.zip"
vendor_dir = root_dir / ".tmp"

# Ensure cache folder exists
vendor_dir.parent.mkdir(parents=True, exist_ok=True)

# 🧹 Clean output directory completely
if output_dir.exists():
    shutil.rmtree(output_dir)
output_dir.mkdir(parents=True, exist_ok=True)

# Create temp build folder
tmp_dir.mkdir(parents=True, exist_ok=True)

# Copy vendor dependencies if exist
if vendor_dir.exists():
    shutil.copytree(vendor_dir, tmp_dir, dirs_exist_ok=True)
    print(f"📦 Copied vendor dependencies from {vendor_dir}")
else:
    print(f"⚠️ No cached vendor dependencies found at {vendor_dir}")

# Copy source files (excluding __pycache__)
for item in be_function_dir.iterdir():
    if item.name in {"__pycache__", ".pytest_cache", ".mypy_cache"}:
        continue
    dest = tmp_dir / item.name
    if item.is_dir():
        shutil.copytree(item, dest)
    else:
        shutil.copy2(item, dest)

# Remove static folder if STATIC_FILES_DIR is set
static_dir_name = os.environ.get("STATIC_FILES_DIR", "static")
if static_dir_name:
    static_dir = tmp_dir / static_dir_name
    if static_dir.exists():
        shutil.rmtree(static_dir)
        print(f"🗑 Removed static folder: {static_dir}")

for path in tmp_dir.rglob("*"):
    if path.is_dir():
        if path.name in {"__pycache__", "tests", "test", "testing"}:
            shutil.rmtree(path)
    else:
        if path.suffix in {".pyc", ".pyo"}:
            path.unlink()

# Create zip
print(f"📦 Creating zip: {zip_file}")
subprocess.run(["zip", "-r", "-9", str(zip_file), "."], cwd=tmp_dir, check=True)

# Clean up temp folder
shutil.rmtree(tmp_dir)

print(f"✅ Lambda zip created at {zip_file}")
