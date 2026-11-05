"""Real crypto/native inert FDs within a declared synthetic process-inventory/RAM fixture.

Only the current fixture worker and its owned children are in the unit inventory. Global
absence, including unrelated protected user processes, requires disposable native qualification.
"""
from pathlib import Path
import json
import os

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


@pytest.fixture(autouse=True)
def isolated_fixture_process_inventory(monkeypatch):
    """Do not inspect an installed user's processes merely to test synthetic legacy bytes."""
    import psutil

    def inventory(attributes):
        owner = psutil.Process(os.getpid())
        for process in [owner, *owner.children(recursive=True)]:
            try:
                process.info = process.as_dict(attrs=attributes, ad_value=None)
            except psutil.NoSuchProcess:
                continue
            yield process
    monkeypatch.setattr(psutil, 'process_iter', inventory)


def _legacy(home, *, live=False):
    root = home/'cache'/'browser-use'/'lightpanda'
    (root/'http-cache').mkdir(parents=True)
    (root/'http-cache'/'a').write_bytes(b'private original cache A')
    (root/'http-cache'/'b').write_bytes(b'private original cache B')
    (root/'old.log').write_bytes(b'private original diagnostic')
    owner = os.getpid() if live else 2**30
    (root/'old.json').write_text(json.dumps({'pid':owner,'owner_pid':owner,'port':43219,'start_time':123}))
    return root


def test_literal_legacy_import_keeps_full_cipher_readback_without_adopting_old_control(native_state):
    from tools.browser_lightpanda_legacy import import_literal
    from tools.browser_lightpanda_state import LightpandaLease
    from hermes_security import browser_worker_archive as archive
    from hermes_cli import browser_worker_store as store

    home, _user, ram = native_state
    source = _legacy(home)
    original = {p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}
    with pytest.raises(RuntimeError, match='browser.worker.repair'):
        LightpandaLease(home, 'must-not-autoload')
    assert source.exists() and not list(home.glob('browser-worker/*.hbwa'))
    result = import_literal(home)
    assert not source.exists()
    restored = ram/'full-original'; restored.mkdir(mode=0o700)
    archive.replay_archive(home, result['recovery'], custody=archive.capture_custody(home), target=restored)
    assert {p.relative_to(restored).as_posix():p.read_bytes() for p in restored.rglob('*') if p.is_file()} == original
    assert all(value not in (home/result['recovery']).read_bytes() for value in original.values())
    selected = store.resource(home, kind='lightpanda-http-cache')
    assert not list((selected.path/'processes').iterdir())
    assert (selected.tree/'old.json').read_bytes() == original['old.json']
    lease = LightpandaLease(home, 'new')
    with lease.access() as call:
        lease.prepare(call)
    assert (lease.cache/'a').read_bytes() == original['http-cache/a']


@pytest.mark.parametrize('replacement', ['live-owner','source-alias'])
def test_legacy_live_or_aliased_source_is_retained_before_archive(native_state, replacement):
    from tools.browser_lightpanda_legacy import import_literal

    home, _user, _ram = native_state
    source = _legacy(home, live=replacement == 'live-owner')
    original = (source/'old.json').read_bytes()
    if replacement == 'source-alias':
        retained = source.with_name('retained-original')
        source.rename(retained)
        source.symlink_to(retained, target_is_directory=True)
    with pytest.raises((RuntimeError, OSError), match='live or unknown|alias'):
        import_literal(home)
    assert (source/'old.json').read_bytes() == original
    assert not list(home.glob('browser-worker/*.hbwa'))


@pytest.mark.parametrize('new_data', [False, True], ids=['unchanged-survivors-retry','new-survivor-refuses'])
def test_interrupted_source_retirement_keeps_original_full_cipher_for_safe_retry(native_state, monkeypatch, new_data):
    from tools.browser_lightpanda_legacy import import_literal
    from hermes_security import browser_worker_archive as archive
    from hermes_cli import browser_worker_store as store

    home, _user, ram = native_state
    source = _legacy(home)
    info = (source/'http-cache').stat()
    identity = info.st_dev, info.st_ino
    unlink = os.unlink
    fired = []
    def interrupt(name, *, dir_fd=None):
        actual = os.fstat(dir_fd) if dir_fd is not None else None
        if not fired and actual is not None and (actual.st_dev,actual.st_ino) == identity:
            unlink(name, dir_fd=dir_fd)
            fired.append(True)
            raise OSError('synthetic first-entry interruption')
        return unlink(name, dir_fd=dir_fd) if dir_fd is not None else unlink(name)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'unlink', interrupt)
        with pytest.raises(RuntimeError, match='first-entry interruption'):
            import_literal(home)
    assert fired and source.exists()
    selected = store.resource(home, kind='lightpanda-http-cache')
    restored = ram/'saved-import'; restored.mkdir(mode=0o700)
    archive.replay_archive(home, selected.relative, custody=archive.capture_custody(home), target=restored)
    marker = json.loads((restored/'.lightpanda-original-import.json').read_text())
    recovery = home/marker['recovery']
    original_cipher = recovery.read_bytes()
    if new_data:
        (source/'http-cache'/'new').write_bytes(b'new work must not disappear')
        before = {p.relative_to(source):p.read_bytes() for p in source.rglob('*') if p.is_file()}
        with pytest.raises(RuntimeError, match='New worker payload'):
            import_literal(home)
        assert {p.relative_to(source):p.read_bytes() for p in source.rglob('*') if p.is_file()} == before
    else:
        result = import_literal(home)
        assert result['recovery'] == marker['recovery'] and not source.exists()
    assert recovery.read_bytes() == original_cipher


def test_open_original_log_writer_is_retained_until_close_then_full_snapshot_recovers(native_state):
    import subprocess
    import sys
    from tools.browser_lightpanda_state import legacy_path
    from tools.browser_lightpanda_legacy import import_literal
    from hermes_security import browser_worker_archive as archive

    home, _user, ram = native_state
    source = legacy_path(home)
    source.mkdir(mode=0o700, parents=True)
    target = source/'manual-session.log'
    payload = b'original live stderr complete before retirement'
    target.write_bytes(payload)
    unrelated = source.parent/'unrelated-readable-file'
    unrelated.write_bytes(b'unrelated public file')
    script = source.parent/'serve'
    script.write_text('import threading\nprint("ready",flush=True)\nthreading.Event().wait()\n')
    with target.open('ab') as output:
        child = subprocess.Popen([sys.executable,'serve','--host','127.0.0.1','--port','49317'],
                                 cwd=source.parent, stdout=subprocess.PIPE, stderr=output)
    try:
        assert child.stdout.readline() == b'ready\n'
        with pytest.raises(RuntimeError, match='still open'):
            import_literal(home)
        assert target.read_bytes() == payload and child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)
    # A readable unrelated path cannot masquerade as ownership of the selected original tree.
    with unrelated.open('rb'):
        report = import_literal(home)
    assert not source.exists() and unrelated.read_bytes() == b'unrelated public file'
    destination = ram/'verified-original-log'
    destination.mkdir(mode=0o700)
    archive.replay_archive(home, Path(report['recovery']), custody=archive.capture_custody(home), target=destination)
    assert (destination/'manual-session.log').read_bytes() == payload
