"""Exercise the gate in real pytest processes, including collection skips."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(("source", "strict", "expected"), [
    ("def test_ok(): assert True\n", True, 0),
    ("import pytest\ndef test_skip(): pytest.skip('missing capability')\n", True, 1),
    ("import pytest\npytest.skip('missing module', allow_module_level=True)\n", True, 1),
    ("import pytest\ndef test_skip(): pytest.skip('optional locally')\n", False, 0),
    ("import pytest\n@pytest.mark.xfail(reason='known issue')\ndef test_bad(): assert False\n", True, 1),
    ("def test_bad(): assert False\n", True, 1),
])
def test_skip_gate(tmp_path, source, strict, expected):
    shutil.copyfile(Path(__file__).with_name("conftest.py"), tmp_path / "conftest.py")
    (tmp_path / "test_fixture.py").write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="")
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if strict:
        command.append("--fail-on-skip")
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == expected, result.stdout + result.stderr
    if strict and ("skip(" in source or "xfail" in source):
        assert "--fail-on-skip: skipped tests are not allowed" in result.stdout
