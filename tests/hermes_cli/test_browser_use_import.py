"""Explicit import preserves complete data without executing helper code; real crypto/unit RAM seam."""
from pathlib import Path
import hashlib

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


@pytest.fixture
def importer(native_state, monkeypatch):
    from hermes_cli import browser_use_import as imports

    monkeypatch.setattr(imports, 'require_harness_source_idle', lambda _source, **_kwargs: None)
    return imports


def _source(user):
    source = user/'original-workspace'
    source.mkdir(mode=0o755)
    (source/'CacheStorage.tmp').write_bytes(b'complete original private application data')
    (source/'agent_helpers.py').write_text('raise RuntimeError("Import must not execute this module")\n')
    (source/'agent_helpers.py').chmod(0o700)
    return source


def test_explicit_import_preserves_bytes_execute_mapping_and_ciphertext_backup(native_state, importer):
    from hermes_cli import browser_worker_store as store
    from hermes_security import browser_worker_archive as archive
    from hermes_cli import backup_browser_profiles as backup

    home, user, ram = native_state
    source = _source(user)
    original = (source/'CacheStorage.tmp').read_bytes()
    report = importer.import_source(home, source, kind='workspace')
    assert report['source_retired'] and not source.exists()
    selected = importer.mapped_resource(home, source, 'workspace')
    with selected.operation():
        record = importer.check_mapping(selected, source, kind='workspace')
        assert record['source'] == str(source) and record['recovery'] == report['recovery']
        assert (selected.tree/'CacheStorage.tmp').read_bytes() == original
        assert (selected.tree/'agent_helpers.py').stat().st_mode & 0o7777 == 0o700
    assert selected.tree.is_relative_to(ram)
    for relative in (Path(report['archive']), Path(report['recovery'])):
        assert original not in (home/relative).read_bytes()
        assert backup.current_archive(relative)
        with (home/relative).open('rb') as handle:
            archive.authenticate_ciphertext(handle, relative, custody=selected.custody)
    source.mkdir(mode=0o755)
    (source/'new-only-copy').write_bytes(b'new raw source must not be ignored')
    with selected.operation():
        with pytest.raises(store.WorkerStateError, match='reappeared'):
            importer.check_mapping(selected, source, kind='workspace')
    assert (source/'new-only-copy').read_bytes() == b'new raw source must not be ignored'


@pytest.mark.parametrize('change', ['partial', 'new-file', 'changed-file', 'parent'])
def test_import_retry_uses_original_full_source_proof(native_state, importer, monkeypatch, change):
    from hermes_cli import browser_worker_store as store, browser_worker_retirement as retirement
    from hermes_security import browser_worker_archive as archive

    home, user, ram = native_state
    source = _source(user)
    original_helper = (source/'agent_helpers.py').read_bytes()
    delete = retirement._remove_contents
    def interrupted(*_args):
        (source/'CacheStorage.tmp').unlink()
        raise OSError('Declared interrupted source cleanup')
    monkeypatch.setattr(retirement, '_remove_contents', interrupted)
    with pytest.raises(store.WorkerStateError, match='repeat the same explicit'):
        importer.import_source(home, source, kind='workspace')
    selected = importer.mapped_resource(home, source, 'workspace')
    original_cipher = (home/selected.relative).read_bytes()
    monkeypatch.setattr(retirement, '_remove_contents', delete)
    if change == 'new-file':
        (source/'new-only-copy').write_bytes(b'new application progress')
    elif change == 'changed-file':
        (source/'agent_helpers.py').write_bytes(b'changed sole copy')
    elif change == 'parent':
        source.parent.rename(source.parent.with_name(source.parent.name+'-original'))
        source.parent.mkdir(mode=0o700)
        source.mkdir(mode=0o755)
        (source/'agent_helpers.py').write_bytes(original_helper)
    if change != 'partial':
        before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.is_file()}
        with pytest.raises(store.WorkerStateError):
            importer.import_source(home, source, kind='workspace')
        assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.is_file()} == before
    else:
        report = importer.import_source(home, source, kind='workspace')
        assert report['source_retired'] and not source.exists()
        verified = ram/'complete-original-readback'; verified.mkdir(mode=0o700)
        archive.replay_archive(home, selected.relative, custody=selected.custody, target=verified)
        assert (verified/'CacheStorage.tmp').read_bytes() == b'complete original private application data'
        assert (verified/'agent_helpers.py').read_bytes() == original_helper
    assert (home/selected.relative).read_bytes() == original_cipher


@pytest.mark.parametrize('relative', ['cache/browser-use/workspace/default/progress.log',
                                     'cache/browser-use/escaped-task-data.tmp'])
def test_known_legacy_private_family_is_reported_without_wrapping_or_omitting_files(native_state, relative):
    from hermes_security.migrate import scan_for_plaintext, repair_clobbered_state
    from hermes_cli.backup_browser_profiles import skip_import

    home, _user, _ram = native_state
    path = home/relative; path.parent.mkdir(mode=0o700, parents=True)
    payload = b'original legacy data requires deliberate whole-tree conversion'
    path.write_bytes(payload)
    before = path.stat()
    assert scan_for_plaintext(home)
    assert skip_import(Path(relative))
    report = repair_clobbered_state(home)
    assert report['browser_retained']
    assert path.read_bytes() == payload and path.stat().st_ino == before.st_ino
