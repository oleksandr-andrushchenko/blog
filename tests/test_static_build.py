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
        '8e0cfa67-ec77-4175-a26a-5c58f4876959.mp4': b'UUID fixture',
        'f1f76e3c-1e14-46a5-ac93-4318e28c462c_960x540.webp': b'UUID fixture',
        'a7ad8a2f-a10b-42ab-949a-2f0289b51198_640x640.webp': b'UUID fixture',
        'aedecb19-0624-4dc0-a40a-226099cca935_640x640.webp': b'UUID fixture',
        '414f4b09-0464-4df9-8909-b509f537d163.mp4': b'UUID fixture',
        'bf170c2e-8569-464e-bd60-52007808d166_540x960.webp': b'UUID fixture',
        'nested/f1f76e3c-1e14-46a5-ac93-4318e28c462c_960x540.webp': b'UUID nested fixture',
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
    deployable = {'styles.css', 'favicon.ico', 'nested/og_1200x630.png'}
    assert {str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()} == deployable
    for filename, content in files.items():
        assert (static / filename).read_bytes() == content
        if filename in deployable:
            assert (output / filename).read_bytes() == content
