"""An older updater's detached completion still has an interactive stdin."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.platforms("posix")
def test_detached_completion_can_unlock_using_inherited_terminal(tmp_path):
    import pty
    import select

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
from hermes_cli.vault_cmd import _password_from_env_or_prompt
assert _password_from_env_or_prompt() == "test-terminal-password"
print("unlocked")
''', encoding="utf-8")
    master, slave = pty.openpty()
    try:
        with subprocess.Popen([sys.executable, "-I", str(probe), str(source)],
                              stdin=slave, start_new_session=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                assert select.select([process.stderr], [], [], 15)[0], "no password prompt"
                os.write(master, b"test-terminal-password\n")
                output, error = process.communicate(timeout=20)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    finally:
        os.close(master)
        os.close(slave)
    assert process.returncode == 0, output + error
    assert output == "unlocked\n"
