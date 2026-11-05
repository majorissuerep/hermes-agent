"""Native helper processes only: no Lean/Hermes app, model or RAM proof on the host."""
import json
import os
import subprocess
import sys

import pytest

from hermes_platform.checker_process import CheckerProcessError, capture_executable, run

pytestmark = pytest.mark.platforms("linux", "macos")


def _selected(tmp_path, body):
    root, directory = tmp_path / 'tool', tmp_path / 'empty'
    root.mkdir(mode=0o700)
    directory.mkdir(mode=0o700)
    root.chmod(0o700)
    directory.chmod(0o700)
    executable = root / 'fixture-checker'
    executable.write_text(f'#!{sys.executable}\n' + body)
    executable.chmod(0o700)
    return capture_executable(executable, root=root), directory


def test_checker_has_only_expected_stdin_and_no_owner_environment_or_inheritable_fd(tmp_path, monkeypatch):
    reader, writer = os.pipe()
    os.set_inheritable(reader, True)
    inode = os.fstat(reader).st_ino
    selected, directory = _selected(tmp_path, f'''import json,os,sys
try: inherited=os.fstat({reader}).st_ino=={inode}
except OSError: inherited=False
print(json.dumps({{'environment':sorted(os.environ),'home':os.environ['HOME'],
                  'tmp':os.environ['TMPDIR'],'stdin':sys.stdin.read(),'probe_fd':inherited}}))
''')
    monkeypatch.setenv('HERMES_SESSION_VAULT', 'synthetic-owner-grant')
    monkeypatch.setenv('HERMES_VAULT_KEY_FD', str(reader))
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-owner-credential')
    monkeypatch.setenv('LEAN_PATH', '/untrusted/library')
    try:
        for source in (None, 'public generated formula'):
            result = run(selected, [], directory=directory, source=source, timeout=10)
            observed = json.loads(result.stdout)
            assert {'HOME', 'LANG', 'TMPDIR'} <= set(observed['environment'])
            assert set(observed['environment']) <= {'HOME', 'LANG', 'TMPDIR', 'LC_CTYPE'}
            assert observed['home'] == observed['tmp'] == str(directory)
            assert observed['stdin'] == (source or '') and observed['probe_fd'] is False
            assert result.returncode == 0 and list(directory.iterdir()) == []
        assert os.fstat(reader).st_ino == inode  # child closure did not close its parent's FD
    finally:
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize('effect', ['overflow', 'file-write'])
def test_unexpected_checker_outputs_cannot_be_accepted(tmp_path, effect):
    body = ('import sys\nsys.stdout.write("x"*70000)\n' if effect == 'overflow' else
            'from pathlib import Path\nPath("unexpected").write_text("public fixture")\n')
    selected, directory = _selected(tmp_path, body)
    with pytest.raises(CheckerProcessError, match='output bound|unexpected files'):
        run(selected, [], directory=directory, timeout=10)


def test_replaced_unchanged_named_executable_is_refused_and_fresh_identity_remains_usable(tmp_path):
    selected, directory = _selected(tmp_path, 'print("original")\n')
    selected.path.rename(selected.path.with_name('original-inode'))
    selected.path.write_text(f'#!{sys.executable}\nprint("replacement")\n')
    selected.path.chmod(0o700)
    with pytest.raises(CheckerProcessError, match='changed'):
        run(selected, [], directory=directory)
    fresh = capture_executable(selected.path, root=selected.path.parent)
    assert run(fresh, [], directory=directory).stdout.strip() == 'replacement'


def test_checker_timeout_cancels_only_its_supervised_process(tmp_path):
    selected, directory = _selected(tmp_path, 'import threading\nthreading.Event().wait()\n')
    with pytest.raises(subprocess.TimeoutExpired):
        run(selected, [], directory=directory, timeout=2)
    assert list(directory.iterdir()) == []
