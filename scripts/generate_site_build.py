import os
import re
import shutil
from pathlib import Path

static_files_dir = os.getenv("STATIC_FILES_DIR", "static")
assets_dir = Path(__file__).parent.parent / static_files_dir
output_dir = Path(__file__).parent.parent / ".site-build"

uuid_prefix_pattern = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

# 🧹 Clean output directory
if output_dir.exists():
    for item in output_dir.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
else:
    output_dir.mkdir(parents=True)


# 🪄 Copy deployable assets, excluding local fixtures and generated files.
def copy_static_files(src: Path, dst: Path):
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            target.mkdir(exist_ok=True)
            copy_static_files(item, target)
        elif item.is_file():
            # Synthetic media is used only by local/test fixtures.
            if item.name.startswith("dummy-"):
                print(f"⏭️  Skipping local fixture: {item.name}")
                continue
            # The API generates the live sitemap from production data. Local test
            # runs also write this file; never deploy that copy over production.
            if re.fullmatch(r"sitemap(?:-\d+)?\.xml", item.name):
                continue
            if uuid_prefix_pattern.match(item.name):
                print(f"⏭️  Skipping file: {item.name}")
                continue
            shutil.copy2(item, target)


# 📦 Copy production assets without local fixtures or generated catalog files.
copy_static_files(assets_dir, output_dir)
print("🎉 Copied static files successfully (excluded local fixtures and generated files)")
