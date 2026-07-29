import shutil
import subprocess
from pathlib import Path

root_dir = Path(__file__).parent.parent
source_dir = root_dir / "image-variants-lambda"
output_dir = root_dir / ".code-build"
tmp_dir = output_dir / "tmp_image_variants"
zip_file = output_dir / "image-variants-function.zip"

if tmp_dir.exists():
    shutil.rmtree(tmp_dir)
tmp_dir.mkdir(parents=True)

subprocess.run([
    "pip", "install", "--no-cache-dir", "-r", str(source_dir / "requirements.txt"),
    "-t", str(tmp_dir),
], check=True)
shutil.copy2(source_dir / "handler.py", tmp_dir / "handler.py")
subprocess.run(["zip", "-r", "-9", str(zip_file), "."], cwd=tmp_dir, check=True)
shutil.rmtree(tmp_dir)
print(f"Created {zip_file}")
