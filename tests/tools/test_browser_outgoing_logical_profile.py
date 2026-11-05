"""Real crypto interoperability for logical-profile outgoing state and native proofs.

Only synthetic filesystem/crypto helpers run here; no vendor, transport or CLI is
started. Native combined outgoing/browser acceptance remains a separate gate.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest_plugins = ["tests.hermes_security.test_profile_resource_binding"]
pytestmark = pytest.mark.platforms("posix")


def _lease(home, custody):
    from hermes_security import browser_profile_archive as archive

    slot = archive.OUTGOING_SLOT_PREFIX + "ssh"
    identity = {"backend": "ssh", "selection": "1" * 64, "uid": os.getuid(),
                "home": "/synthetic-account", "machine": "2" * 64, "daemon": None}
    digest = archive.identity_digest(custody, slot, identity)
    return SimpleNamespace(home=home, custody=custody, slot=slot, identity=identity,
                           digest=digest, token="a" * 32,
                           relative=archive.archive_relative(slot, digest))


def _document(lease, saved, metadata, summary):
    from hermes_platform.remote_identity import RuntimeIdentity
    from tools.browser_recording_pending import _owner_identity

    generation = RuntimeIdentity("synthetic-incarnation",
                                 tuple(("/synthetic/" + str(i), "1:2:3:4") for i in range(5)))
    return {"phase": "checkpointed", "lease": lease.token, "owner": _owner_identity(),
            "identity": lease.identity, "origin": {}, "backend": "ssh",
            "runtime": asdict(generation), "archive": lease.relative.as_posix(),
            "raw_hash": "3" * 64, "maximum": 1024**2, "recovery": asdict(summary),
            "archive_cipher_sha256": saved.digest, "inventory": metadata}


def test_named_parent_key_profile_preserves_outgoing_proof_and_reference_scope(machine, tmp_path):
    from agent.session_context import scoped_current_session_id
    from hermes_security import artifacts, io, browser_profile_archive as archive
    from hermes_security.errors import VaultError
    from hermes_security.operation_custody import guarded
    from hermes_security.session_authority import start_session_authority
    from hermes_security.session_custody import SessionCustody
    from tests.hermes_security.test_profile_resource_binding import _profile
    from tools import browser_outgoing_inventory as inventory
    from tools import browser_outgoing_retirement as retirement, browser_outgoing_state as state

    home, sibling_home = _profile("outgoing-a"), _profile("outgoing-b")
    first = start_session_authority(home, "outgoing-first")
    second = start_session_authority(home, "outgoing-second")
    try:
        with scoped_current_session_id(first.session_id):
            old = _lease(home, guarded(SessionCustody.capture(home)))
            assert old.custody.resource.vault.home == machine
            source = tmp_path / "synthetic-browser"
            source.mkdir(mode=0o700)
            (source / "Cookies").write_bytes(b"private synthetic cookie")
            written = archive.write_archive(home, old.relative, source, custody=old.custody,
                                            slot=old.slot, identity=old.identity, digest=old.digest)
            ram = tmp_path / "synthetic-proof"
            ram.mkdir(mode=0o700)
            native = {"version": 1, "root": [1, 2], "entries": {}}
            with retirement.pinned_archive(old) as saved, inventory.published(old, ram, native) as (metadata, proof):
                state.write(home, old.digest, old.custody, _document(old, saved, metadata, written))
                reference = state.read(home, old.digest, old.custody)
                assert reference["version"] == 2
                assert json.loads(io.read_bytes(home / state.relative(old.digest), purpose="state")) == reference
                assert inventory.original(old, metadata, proof, tmp_path) == native
                cipher = (home / state.relative(old.digest)).read_bytes()
                proof_cipher = (home / metadata["relative"]).read_bytes()
                assert b"private synthetic cookie" not in (home / old.relative).read_bytes()
        first.close()
        with scoped_current_session_id(second.session_id):
            with pytest.raises(VaultError):
                state.read(home, old.digest, old.custody)
            fresh = _lease(home, guarded(SessionCustody.capture(home)))
            assert fresh.digest == old.digest
            assert state.read(home, fresh.digest, fresh.custody) == reference
            with inventory.opened(fresh, metadata) as proof:
                proof.check()
            state.remove(home, fresh.digest, fresh.custody, fresh.token)
            assert not (home / state.relative(fresh.digest)).exists()
        other = _lease(sibling_home, guarded(SessionCustody.capture(sibling_home)))
        for relative, data in [(state.relative(old.digest), cipher), (Path(metadata["relative"]), proof_cipher)]:
            artifacts._publish(sibling_home, relative, data, other.custody, overwrite=False)
        other.digest = old.digest  # Even a matching copied resource name cannot authorize sibling data.
        with pytest.raises(VaultError):
            state.read(sibling_home, old.digest, other.custody)
        with pytest.raises(VaultError):
            with inventory.opened(other, metadata):
                pytest.fail("A sibling profile authenticated another profile's native retirement proof")
        assert (sibling_home / state.relative(old.digest)).read_bytes() == cipher
        assert (sibling_home / metadata["relative"]).read_bytes() == proof_cipher
    finally:
        first.close()
        second.close()
