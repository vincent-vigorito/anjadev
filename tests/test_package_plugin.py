"""Runtime packaging includes new modules, but excludes author state and unsafe links."""
import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import package_plugin  # noqa: E402


def test_runtime_inventory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'plugin.tar.gz'
    report = package_plugin.build(root, output)
    assert report['version'] == json.loads((root / '.codex-plugin/plugin.json').read_text())['version']
    assert 'scripts/index_pipeline.py' in report['files']
    assert 'scripts/anja/persistence.py' in report['files']
    assert 'scripts/code_inspect.py' in report['files']
    assert report['files']['plugins/anja/scripts']['link'] == '../../scripts'
    with tarfile.open(output) as archive:
        names = archive.getnames()
    assert 'anja/.mcp.json' not in names
    assert not any('/.anjawiki/' in n or '/__pycache__/' in n or n.endswith('.env') for n in names)
    assert 'anja/AGENTS.md' not in names


def test_external_symlink_is_rejected(tmp_path):
    root = tmp_path / 'plugin'
    root.mkdir()
    (root / 'runtime').symlink_to(tmp_path, target_is_directory=True)
    original = package_plugin.ROOTS
    try:
        package_plugin.ROOTS = ('runtime',)
        with pytest.raises(ValueError, match='escapes root'):
            package_plugin.build(root, tmp_path / 'invalid.tar.gz')
    finally:
        package_plugin.ROOTS = original
