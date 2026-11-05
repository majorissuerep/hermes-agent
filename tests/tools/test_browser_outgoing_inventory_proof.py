"""Immutable native retirement evidence uses the established authenticated archive readers."""
import os
from pathlib import Path
import stat
import zipfile

import pytest

pytest_plugins = ["tests.tools.test_browser_outgoing_continuity"]
pytestmark = pytest.mark.platforms("linux")


def _setup(outgoing, name):
    from tools import browser_outgoing_inventory as inventory, browser_outgoing_profile as profiles

    profile = outgoing.start(name)
    (profile / "Cookies").write_bytes(os.urandom(32))
    lease = profiles.lookup(name)
    ram = outgoing.ram / name
    ram.mkdir(mode=0o700)
    document, _, _ = inventory.receive(lease, ram, "original.json")
    return profile, lease, ram, document


def test_immutable_proof_collision_preserves_original_cipher_inode_and_source(outgoing, monkeypatch):
    from tools import browser_outgoing_inventory as inventory, browser_outgoing_state as state

    profile, lease, ram, document = _setup(outgoing, "proof-collision")
    before = (profile / "Cookies").read_bytes()
    monkeypatch.setattr(inventory.secrets, "token_hex", lambda amount: "f" * (amount * 2))
    with state.locked(lease.home, lease.digest, lease.base):
        with inventory.published(lease, ram, document) as (metadata, pinned):
            path = lease.home / metadata["relative"]
            cipher, info = path.read_bytes(), path.stat()
            other = outgoing.ram / "other-publication"
            other.mkdir(mode=0o700)
            with pytest.raises(FileExistsError):
                with inventory.published(lease, other, document):
                    pytest.fail("Immutable proof unexpectedly overwrote its destination")
            pinned.check()
            assert path.read_bytes() == cipher
            assert (path.stat().st_dev, path.stat().st_ino) == (info.st_dev, info.st_ino)
            assert (profile / "Cookies").read_bytes() == before
            assert not list(path.parent.glob(".stage-*"))


def test_proof_authenticates_in_scanner_ciphertext_backup_and_import(outgoing, tmp_path):
    from hermes_cli import backup_browser_profiles as backup
    from hermes_security import migrate
    from tools import browser_outgoing_inventory as inventory, browser_outgoing_state as state

    _, lease, ram, document = _setup(outgoing, "proof-readers")
    with state.locked(lease.home, lease.digest, lease.base):
        with inventory.published(lease, ram, document) as (metadata, pinned):
            relative = Path(metadata["relative"])
            path = lease.home / relative
            before = path.read_bytes()
            assert migrate._browser_offender(path, relative) is None
            assert relative.as_posix() not in migrate.scan_for_plaintext(lease.home)
            assert (path, relative) in list(backup.current_entries(lease.home, Path()))
            packed = tmp_path / "ciphertext-backup.zip"
            with zipfile.ZipFile(packed, "w") as bundle:
                assert backup.write_ciphertext(bundle, path, relative) is None
            with zipfile.ZipFile(packed) as bundle:
                restored = tmp_path / "private-import-stage"
                restored.write_bytes(bundle.read(relative.as_posix()))
            assert restored.read_bytes() == before
            assert backup.prepare_import(restored, path, lease.home) is True
            assert stat.S_IMODE(restored.stat().st_mode) == 0o600
            pinned.check()
            assert path.read_bytes() == before


@pytest.mark.parametrize("phase", ["before-publication", "after-publication", "readback"])
def test_failed_proof_never_advances_reference_or_retires_source(outgoing, monkeypatch, phase):
    from hermes_security import tree_archive
    from tools import browser_outgoing_inventory as inventory, browser_outgoing_profile as profiles
    from tools import browser_outgoing_state as state
    from tools.browser_tool_lifecycle import cleanup_browser

    profile = outgoing.start("proof-failure")
    (profile / "Cookies").write_bytes(os.urandom(32))
    lease = profiles.lookup("proof-failure")
    before = (profile / "Cookies").read_bytes()
    reference = (lease.home / state.relative(lease.digest)).read_bytes()
    original_link, original_fsync, original_replay = os.link, os.fsync, inventory._replay
    armed, failures = [], []

    def link(source, destination, **kwargs):
        if str(destination).startswith("outgoing-proof-"):
            if phase == "before-publication":
                failures.append(True)
                raise OSError("synthetic proof publication refusal")
            result = original_link(source, destination, **kwargs)
            armed.append(True)
            return result
        return original_link(source, destination, **kwargs)

    def fsync(descriptor):
        if armed and phase == "after-publication" and not failures:
            failures.append(True)
            raise OSError("synthetic parent durability uncertainty")
        return original_fsync(descriptor)

    def replay(*args, **kwargs):
        if phase == "readback":
            failures.append(True)
            raise tree_archive.ArchiveError("synthetic proof readback refused")
        return original_replay(*args, **kwargs)

    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(inventory, "_replay", replay)
    failure_type = OSError if phase == "before-publication" else tree_archive.ArchiveError
    with pytest.raises(failure_type) as error:
        cleanup_browser("proof-failure")
    assert failures
    assert not list((lease.home / "browser-profile").glob(".stage-*"))
    assert (profile / "Cookies").read_bytes() == before
    assert (lease.home / state.relative(lease.digest)).read_bytes() == reference
    assert state.read(lease.home, lease.digest, lease.custody)["phase"] == "active"
    assert profiles.lookup("proof-failure") is lease
    if phase == "after-publication":
        assert "durability" in str(error.value)
        assert list((lease.home / "browser-profile").glob("outgoing-proof-*.hbpa"))
    if phase == "before-publication":
        assert not list((lease.home / "browser-profile").glob("outgoing-proof-*.hbpa"))


@pytest.mark.parametrize("case", ["revoked", "replaced"])
def test_optional_completed_proof_cleanup_preserves_unconfirmed_cipher(outgoing, caplog, case):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from tools import browser_outgoing_inventory as inventory

    source = start_session_authority(outgoing.home, "completed-proof")
    sibling = start_session_authority(outgoing.home, "completed-sibling")
    try:
        with scoped_current_session_id(source.session_id):
            profile, lease, ram, document = _setup(outgoing, "completed-proof")
            original = (profile / "Cookies").read_bytes()
            with inventory.published(lease, ram, document) as (metadata, pinned):
                path = lease.home / metadata["relative"]
                if case == "revoked":
                    source.close()
                else:
                    replacement = path.with_suffix(".replacement")
                    replacement.write_bytes(path.read_bytes())
                    replacement.chmod(0o600)
                    replacement.replace(path)
                cipher, native = path.read_bytes(), path.stat()
                inventory.discard_completed(lease, pinned)
                assert path.read_bytes() == cipher
                assert (path.stat().st_dev, path.stat().st_ino) == (native.st_dev, native.st_ino)
                assert (profile / "Cookies").read_bytes() == original
                assert "cleanup is uncertain; complete checkpoint preserved" in caplog.text
        with scoped_current_session_id(sibling.session_id):
            from hermes_security.session_custody import SessionCustody
            assert len(SessionCustody.capture(lease.home).derive_key("sibling-after-optional-cleanup")) == 32
    finally:
        source.close()
        sibling.close()
