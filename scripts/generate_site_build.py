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


# 🪄 Custom copy function that skips UUID-prefixed files
def copy_static_files(src: Path, dst: Path):
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            target.mkdir(exist_ok=True)
            copy_static_files(item, target)
        elif item.is_file():
            if uuid_prefix_pattern.match(item.name):
                print(f"⏭️  Skipping file: {item.name}")
                continue
            shutil.copy2(item, target)


# 📦 Copy static assets except generated UUID-named files
copy_static_files(assets_dir, output_dir)
print("🎉 Copied static files successfully (skipped UUID-prefixed ones)")

# --- Update robots.txt if it exists ---
robots_file = output_dir / "robots.txt"
if robots_file.exists():
    lines = []

    base_url = os.getenv("WEB_BASE_URL")

    if (output_dir / "sitemap.xml").exists():
        lines.append(f"Sitemap: {base_url}/sitemap.xml")

    if lines:
        with robots_file.open("a") as f:
            f.write("\n")
            f.write("\n".join(lines))
        print(f"📝 Updated robots.txt with {', '.join(lines)}")
else:
    print("⚠️  No robots.txt found in output, skipping update")
