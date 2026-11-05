"""Typed worker archives survive ordinary maintenance and opaque disaster recovery."""
from pathlib import Path
import io
import zipfile

import pytest

from hermes_security import browser_worker_archive as workers, migrate, vault


@pytest.fixture
def worker(tmp_path, monkeypatch):
    home = tmp_path/'owner'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(vault, '_SCRYPT_N', 1024)
    vault.init_vault(home, 'synthetic worker recovery password')
    vault.unlock(home, 'synthetic worker recovery password')
    source = tmp_path/'source'
    source.mkdir(mode=0o700)
    (source/'agent_helpers.py').write_bytes(b'def helper(): return 19\n')
    custody = workers.capture_custody(home)
    identity = {'component': 'synthetic worker recovery'}
    digest = workers.identity_digest(custody, identity)
    relative = workers.archive_relative(digest)
    workers.write_archive(home, relative, source, custody=custody, identity=identity, digest=digest)
    yield home, relative, custody
    vault.clear_vault_cache()


def test_worker_scan_and_repair_authenticate_without_resealing_existing_ciphertext(worker):
    home, relative, custody = worker
    before = {path.relative_to(home): path.read_bytes() for path in home.rglob('*') if path.is_file()}
    assert migrate.scan_for_plaintext(home) == []
    result = migrate.repair_clobbered_state(home)
    assert not result['unrecoverable']
    assert {path.relative_to(home): path.read_bytes() for path in home.rglob('*') if path.is_file()} == before
    workers.replay_archive(home, relative, custody=custody, digest=relative.stem)


def test_worker_backup_import_and_plaintext_export_keep_credential_domain(worker, tmp_path):
    from agent.file_safety import get_read_block_error
    from hermes_cli import backup_browser_profiles as backup
    from hermes_security.recovery_export import _files

    home, relative, _custody = worker
    entries = list(backup.current_entries(home, Path('.')))
    assert entries == [(home/relative, Path(relative))]
    before = (home/relative).read_bytes()
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, 'w') as archive:
        assert backup.write_ciphertext(archive, home/relative, relative) is None
    with zipfile.ZipFile(packed) as archive:
        assert archive.read(str(relative)) == before
    stage = tmp_path/'import-stage'
    stage.write_bytes(before)
    stage.chmod(0o600)
    assert backup.prepare_import(stage, home/relative, home)
    assert (home/relative).read_bytes() == before
    assert get_read_block_error(str(home/relative))
    assert relative not in [rel for _path, rel in _files(home)]
    stage.write_bytes(before[:-1])
    with pytest.raises((OSError, ValueError, vault.VaultError)):
        backup.prepare_import(stage, home/relative, home)
    assert (home/relative).read_bytes() == before
