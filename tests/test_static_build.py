"""Verify production builds exclude local assets without removing fixture files."""
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(os.environ.get('PROJECT_ROOT', Path(__file__).resolve().parents[1]))


def test_static_build_excludes_local_dummy_files_at_every_depth(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    script = scripts / 'generate_site_build.py'
    shutil.copyfile(ROOT / 'scripts/generate_site_build.py', script)
    static = tmp_path / 'static'
    files = {
        'dummy-herbs_640x640.webp': b'local image',
        'dummy-coastal.mp4': b'local video',
        'nested/dummy-coastal_960x540.webp': b'local poster',
        'styles.css': b'body {}',
        'favicon.ico': b'site icon',
        'nested/og_1200x630.png': b'site sharing image',
    }
    for filename, content in files.items():
        source = static / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
    result = subprocess.run(
        ['python3', str(script)], cwd=tmp_path, capture_output=True, text=True,
        env=dict(os.environ, STATIC_FILES_DIR='static'), timeout=15,
    )
    assert result.returncode == 0, result.stderr
    output = tmp_path / '.site-build'
    assert {str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()} == {
        name for name in files if not Path(name).name.startswith('dummy-')
    }
    for filename, content in files.items():
        assert (static / filename).read_bytes() == content
        if not Path(filename).name.startswith('dummy-'):
            assert (output / filename).read_bytes() == content
