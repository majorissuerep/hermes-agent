"""Managed machine-vault profiles retain their logical scope through real consumers.

The worker uses a declared RAM-proof seam and real directory inodes; this is
crypto/operation integration, not native browser or protected-memory evidence.
"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytest_plugins = ("tests.hermes_security.test_profile_resource_binding",)


@contextmanager
def _selected(home, session):
    from agent.session_context import scoped_current_session_id
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(home)
    try:
        with scoped_current_session_id(session):
            yield
    finally:
        reset_hermes_home_override(token)


def test_worker_checkpoint_and_locator_keep_logical_aad_and_original_operation(machine, tmp_path, monkeypatch):
    from hermes_cli.browser_worker_store import WorkerCall
    from hermes_platform.memory_protection import RamProof
    from hermes_security import artifacts, browser_worker_archive as archive, browser_worker_pending as pending, io, vault
    from hermes_security.errors import VaultError
    from hermes_security.operation_custody import OperationBudget, OperationCancelled
    from hermes_security.session_authority import start_session_authority
    from hermes_security.session_custody import SessionCustody
    from tests.hermes_security.test_profile_resource_binding import _profile

    first, second = _profile("worker-first"), _profile("worker-second")
    root = tmp_path / "declared-unit-ram"
    root.mkdir(mode=0o700)
    for name in ("tree", "r", "tmp", "claims", "processes"):
        (root / name).mkdir(mode=0o700)
    marker = b"private parent-owned worker payload"
    (root / "tree" / "config.json").write_bytes(marker)
    monkeypatch.setattr("hermes_cli.browser_worker_store._prove",
                        lambda _path: RamProof(True, "ramfs", "declared unit seam, not native RAM evidence"))
    source_a = start_session_authority(first, "worker-source-A")
    source_b = start_session_authority(second, "worker-source-B")
    try:
        with _selected(first, "worker-source-A"):
            operation = SessionCustody.capture(first)
            identity = {"kind": "logical-profile-consumer-contract"}
            digest = archive.identity_digest(operation, identity)
            relative = archive.archive_relative(digest)
            resource = SimpleNamespace(home=first, path=root, tree=root / "tree", identity=identity,
                                       digest=digest, relative=relative)
            call = WorkerCall(resource, operation)
            call.checkpoint()
            pending.sync(resource, call)
            locator = pending.relative(digest)
            assert pending.read(first, locator, custody=call)["home"] == str(first)
            assert str(first).encode() in io.read_bytes(first / locator, purpose="state")
            original = (first / relative).read_bytes(), (first / locator).read_bytes()
            assert all(marker not in raw for raw in original)
            (tmp_path / "restored").mkdir(mode=0o700)
            with (first / relative).open("rb") as handle:
                archive.replay_opened(handle, relative, custody=call, digest=digest, target=tmp_path / "restored")
            assert (tmp_path / "restored" / "config.json").read_bytes() == marker
            budget = OperationBudget(first)
            guarded = budget.guarded(call)
            budget.cancelled.set()
            with pytest.raises(OperationCancelled):
                pending.sync(resource, guarded)
            source_a.close()
            with pytest.raises(VaultError):
                call.checkpoint()
            assert ((first / relative).read_bytes(), (first / locator).read_bytes()) == original
        with _selected(second, "worker-source-B"):
            own = SessionCustody.capture(second)
            (second / relative).parent.mkdir(mode=0o700)
            (second / relative).write_bytes(original[0])
            (second / relative).chmod(0o600)
            with pytest.raises(VaultError):
                archive.replay_archive(second, relative, custody=own, digest=digest)
            output = second / "cache/spillover/still-live.txt"
            artifacts.write_bytes(output, b"sibling still authorized", home=second, custody=own)
            assert artifacts.read_bytes(output, home=second, custody=own) == b"sibling still authorized"
        assert vault.is_unlocked(machine)
        assert not any((home / ".hermes-vault").exists() for home in (first, second))
    finally:
        source_a.close()
        source_b.close()


def test_generated_pet_parent_profile_keeps_pixels_scope_and_revoked_callback(machine):
    from PIL import Image

    from agent import provider_media
    from agent.pet import private_media, store
    from hermes_security import vault
    from hermes_security.errors import VaultError
    from hermes_security.session_authority import start_session_authority
    from tests.hermes_security.test_profile_resource_binding import _profile

    first, second = _profile("pet-first"), _profile("pet-second")
    source_a = start_session_authority(first, "pet-source-A")
    source_b = start_session_authority(second, "pet-source-B")
    try:
        with _selected(first, "pet-source-A"), provider_media.generation_scope():
            pet = store.register_local_pet(Image.new("RGBA", (4, 18), "navy"), slug="same-pet")
            record = private_media.find("same-pet")
            original = pet.spritesheet.read_bytes()
            assert original.startswith(b"HRMVAULT\0")
            assert private_media.read_image(pet.spritesheet, custody=record[3]).startswith(b"RIFF")
            assert private_media.thumbnail(record).startswith(b"\x89PNG\r\n\x1a\n")
        with _selected(second, "pet-source-B"):
            with pytest.raises(PermissionError, match="different profile"):
                private_media.thumbnail(record)
            with provider_media.generation_scope():
                sibling = store.register_local_pet(Image.new("RGBA", (4, 18), "green"), slug="same-pet")
                sibling_record = private_media.find("same-pet")
            source_a.close()
            assert private_media.thumbnail(sibling_record).startswith(b"\x89PNG\r\n\x1a\n")
            assert sibling.spritesheet != pet.spritesheet
        with _selected(first, "pet-source-A"):
            with pytest.raises(VaultError):
                private_media.read_image(pet.spritesheet, custody=record[3])
            assert pet.spritesheet.read_bytes() == original
        assert vault.is_unlocked(machine)
        assert not any((home / ".hermes-vault").exists() for home in (first, second))
    finally:
        source_a.close()
        source_b.close()
