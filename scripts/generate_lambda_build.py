import shutil
import subprocess
import sys
from pathlib import Path

root_dir = Path(__file__).parent.parent
output_dir = root_dir / ".code-build"
source_dirs = {
    "web-function": root_dir / "web-lambda",
    "api-function": root_dir / "api-lambda",
}
shared_dir = root_dir / "lambda-shared"
vendor_dirs = {
    "web-function": root_dir / ".tmp/web",
    "api-function": root_dir / ".tmp/api",
}


def build(name, excluded):
    tmp = output_dir / f"tmp_{name}"
    archive = output_dir / f"{name}.zip"
    tmp.mkdir(parents=True, exist_ok=True)
    vendor_dir = vendor_dirs[name]
    if vendor_dir.exists(): shutil.copytree(vendor_dir, tmp, dirs_exist_ok=True)
    source_dir = source_dirs[name]
    for item in shared_dir.iterdir():
        if item.name in {"requirements.txt", "Dockerfile", "local_app.py", "__pycache__", ".pytest_cache",
                         ".mypy_cache"}: continue
        dest = tmp / item.name
        shutil.copytree(item, dest, dirs_exist_ok=True) if item.is_dir() else shutil.copy2(item, dest)
    for item in source_dir.iterdir():
        if item.name in {"requirements.txt", "Dockerfile", "local_app.py", "__pycache__", ".pytest_cache",
                         ".mypy_cache"}: continue
        dest = tmp / item.name
        shutil.copytree(item, dest, dirs_exist_ok=True) if item.is_dir() else shutil.copy2(item, dest)
    for name in excluded:
        path = tmp / "templates" / name
        if path.exists(): path.unlink()
    for path in list(tmp.rglob("*")):
        if path.is_dir() and path.name in {"__pycache__", "tests", "test", "testing", "bin"}:
            shutil.rmtree(path)
        elif path.is_file() and path.suffix in {".pyc", ".pyo"}:
            path.unlink()
    subprocess.run(["zip", "-r", "-9", str(archive), "."], cwd=tmp, check=True, stdout=subprocess.DEVNULL)
    shutil.rmtree(tmp)
    print(f"Created {archive}")


output_dir.mkdir(exist_ok=True)
if output_dir.exists():
    for path in output_dir.glob("tmp_*"): shutil.rmtree(path)
web_only = set()
api_only = {"index.html", "articles.html", "article.html", "contacts.html", "edit-article.html",
            "edit-article-tag.html", "edit-user.html", "earn.html", "policy.html", "rules.html", "terms.html",
            "users.html", "user.html", "new-article.html"}

requested = sys.argv[1:] or ["all"]
valid = {"web", "api", "all"}
unknown = set(requested) - valid
if unknown:
    raise SystemExit("Unknown Lambda selection: " + ", ".join(sorted(unknown)))
if "all" in requested or "web" in requested:
    build("web-function", web_only)
if "all" in requested or "api" in requested:
    build("api-function", api_only)
