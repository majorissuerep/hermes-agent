"""An older updater's detached completion still has an interactive stdin."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.platforms("posix")
def test_detached_completion_can_unlock_using_inherited_terminal(tmp_path):
    import pty

    source = Path(__file__).resolve().parents[2]
    probe = tmp_path / "terminal.py"
    probe.write_text('''
import os, sys
sys.path.insert(0, sys.argv[1])
from hermes_cli.vault_gate import _tty_available
assert sys.stdin.isatty()
try:
    fd = os.open("/dev/tty", os.O_RDWR)
except OSError:
    pass
else:
    os.close(fd)
    raise AssertionError("test child unexpectedly has a controlling terminal")
assert _tty_available(), "inherited terminal cannot unlock the update"
''', encoding="utf-8")
    master, slave = pty.openpty()
    try:
        result = subprocess.run([sys.executable, "-I", str(probe), str(source)],
                                stdin=slave, start_new_session=True,
                                capture_output=True, text=True, timeout=15)
    finally:
        os.close(master)
        os.close(slave)
    assert result.returncode == 0, result.stdout + result.stderr
