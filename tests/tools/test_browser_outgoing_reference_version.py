"""Proof-bearing reference semantics cannot be consumed as a legacy raw-hash-only intent."""
from copy import deepcopy

import pytest

pytest_plugins = ["tests.tools.test_browser_outgoing_continuity",
                  "tests.tools.test_browser_outgoing_inventory_refusal"]
pytestmark = pytest.mark.platforms("linux")


@pytest.mark.parametrize("case", ["legacy-with-proof", "missing-proof", "active-proof", "missing-cipher"])
def test_reader_refuses_inconsistent_reference_version_without_effects(pending, case):
    from hermes_security import artifacts, tree_archive
    from hermes_security.session_custody import SessionCustody
    from tools import browser_outgoing_state as state
    from tests.tools.test_browser_outgoing_inventory_refusal import _snapshot

    _, _, profile, lease, document = pending
    assert document["version"] == 2
    changed = deepcopy(document)
    mutations = {
        "legacy-with-proof": lambda: changed.update(version=1),
        "missing-proof": lambda: changed.pop("inventory"),
        "active-proof": lambda: changed.update(phase="active"),
        "missing-cipher": lambda: changed.pop("archive_cipher_sha256"),
    }
    mutations[case]()
    custody = SessionCustody.capture(lease.home)
    relative = state.relative(lease.digest)
    original = (lease.home / relative).read_bytes()
    before = _snapshot(profile)
    # A former writer or damaged authenticated metadata is reader input; this
    # intentional malformed fixture uses the existing codec, never a fake key.
    encoded = custody.vault.encrypt(tree_archive.canonical_json(changed), purpose="state",
                                    relpath=relative.as_posix(), authority=custody.authority)
    artifacts._publish(lease.home, relative, encoded, custody, overwrite=True)
    try:
        with pytest.raises(state.OutgoingProfileError):
            state.read(lease.home, lease.digest, custody)
        assert (lease.home / relative).read_bytes() == encoded
        assert _snapshot(profile) == before
    finally:
        artifacts._publish(lease.home, relative, original, custody, overwrite=True)


def test_writer_rejects_invalid_proof_phase_before_replacing_reference(pending):
    from hermes_security.session_custody import SessionCustody
    from tools import browser_outgoing_state as state

    _, _, _, lease, document = pending
    before = (lease.home / state.relative(lease.digest)).read_bytes()
    with pytest.raises(state.OutgoingProfileError, match="version/proof"):
        state.write(lease.home, lease.digest, SessionCustody.capture(lease.home),
                    {**document, "phase": "active"})
    assert (lease.home / state.relative(lease.digest)).read_bytes() == before
