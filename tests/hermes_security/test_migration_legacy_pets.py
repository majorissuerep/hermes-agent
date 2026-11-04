"""Legacy-only private producers participate in encrypted rollback and exact resume.

Pure/helper contracts: synthetic media and real vault codecs, no CLI/app/network.
"""
import json
from pathlib import Path

import pytest

from agent.pet import private_media, store
from hermes_security import migrate, migration_state, migration_workflow, vault
from hermes_security.artifacts import _Custody
from hermes_security.migration_archive import verified_tar
from tests.agent.test_generated_pet_custody import _legacy_pet

PASSWORD = "synthetic legacy producer migration credential"


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    home = tmp_path / "selected"
    source, image, metadata = _legacy_pet(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    yield home, source, image, metadata
    vault.clear_vault_cache()


def _members(backup, home):
    with verified_tar(backup, custody=_Custody(home)) as archive:
        return {member.name: (archive.extractfile(member).read(), member.pax_headers.get("HERMES.kind"))
                for member in archive if member.isfile()}


def test_legacy_only_plan_and_rollback_preserve_original_bytes_and_opaque_child(legacy):
    home, source, image, metadata = legacy
    child = home / "profiles/independent"
    child.parent.mkdir()
    child_source, _child_image, _child_metadata = _legacy_pet(child)
    vault.init_vault(child, "independent synthetic child credential", _allow_existing_state=True)
    child_before = {path.relative_to(home).as_posix(): path.read_bytes()
                    for path in child.rglob("*") if path.is_file()}
    original = {path.relative_to(home).as_posix(): path.read_bytes() for path in source.iterdir()}
    public = {path: path.read_bytes() for path in (home / "cache/pet-catalog").rglob("*") if path.is_file()}
    planned = migrate.migrate_home(home, PASSWORD, dry_run=True)
    assert planned.ok and "pets/legacy-animal/pet.json" in planned.envelopes
    assert not vault.vault_exists(home)

    report = migrate.migrate_home(home, PASSWORD)
    assert report.ok and report.backup_path is not None, report.failures
    backup = report.backup_path
    assert backup.suffix == ".hmb" and metadata["description"].encode() not in backup.read_bytes()
    assert image not in backup.read_bytes()
    members = _members(backup, home)
    for name, data in original.items():
        assert members[name] == (data, "pet")
    for name, data in child_before.items():
        if not name.startswith("profiles/independent/cache/pet-catalog/"):
            assert members[name] == (data, "profile")
        assert (home / name).read_bytes() == data
    assert not any(name.startswith("cache/pet-catalog/") for name in members)
    assert {path: path.read_bytes() for path in public} == public
    assert child_source.exists() and not source.exists()
    saved = backup.read_bytes()
    vault.clear_vault_cache()
    vault.unlock(home, PASSWORD)  # original credential, independent cold resource
    pet = store.load_pet("legacy-animal")
    assert pet.description == metadata["description"]
    assert private_media.read_image(pet.spritesheet) == image
    assert _members(backup, home)["pets/legacy-animal/pet.json"][0] == original["pets/legacy-animal/pet.json"]
    assert backup.read_bytes() == saved
    assert list(home.parent.glob("hermes-premigration-*.tar")) == []


@pytest.mark.parametrize("change", ["drift", "new-input"])
def test_group_drift_refuses_before_retirement_then_completed_conversion_resumes(legacy, monkeypatch, change):
    home, source, image, metadata = legacy
    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt("synthetic stop after verified encrypted rollback")
    with monkeypatch.context() as paused:
        paused.setattr(migration_workflow, "_convert_member", interrupt)
        with pytest.raises(KeyboardInterrupt):
            migrate.migrate_home(home, PASSWORD)
    assert migration_state.pending(home)
    backup = migrate._migration_backups(home)[0]
    encrypted = backup.read_bytes()
    target = source / ("pet.json" if change == "drift" else "new-private-note.txt")
    original_metadata = (source / "pet.json").read_bytes()
    before_target = target.read_bytes() if target.exists() else None
    target.write_text(json.dumps({**metadata, "description": "changed after backup"}) if change == "drift" else "new private input")
    before = {path: path.read_bytes() for path in source.iterdir()}
    refused = migrate.migrate_home(home, PASSWORD)
    assert not refused.ok and refused.backup_path == backup
    assert {path: path.read_bytes() for path in before} == before
    assert not (home / private_media.ROOT).exists()
    assert backup.read_bytes() == encrypted
    assert _members(backup, home)["pets/legacy-animal/pet.json"][0] == original_metadata
    if before_target is None:
        target.unlink()
    else:
        target.write_bytes(before_target)

    write = migration_state.write
    def stop_before_clear(selected, unlocked, state):
        if state is None:
            raise RuntimeError("synthetic stop after complete verified pet retirement")
        return write(selected, unlocked, state)
    with monkeypatch.context() as paused:
        paused.setattr(migration_state, "write", stop_before_clear)
        with pytest.raises(RuntimeError, match="synthetic stop"):
            migrate.migrate_home(home, PASSWORD)
    assert not source.exists() and migration_state.pending(home)
    sealed = {path: path.read_bytes() for path in (home / private_media.ROOT).rglob("*") if path.is_file()}
    completed = migrate.migrate_home(home, PASSWORD)
    assert completed.ok and not migration_state.pending(home), completed.failures
    assert {path: path.read_bytes() for path in sealed} == sealed
    assert private_media.read_image(store.load_pet("legacy-animal").spritesheet) == image
    assert backup.read_bytes() == encrypted
