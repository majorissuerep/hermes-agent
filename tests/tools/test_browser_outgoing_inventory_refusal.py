"""Unavailable or changed original proof cannot authorize destruction of survivors."""
from contextlib import ExitStack
import hashlib
import os
import socket

import pytest

pytest_plugins = ["tests.tools.test_browser_outgoing_continuity"]
pytestmark = pytest.mark.platforms("linux")


def _snapshot(root):
    found = {}
    for path in root.rglob("*"):
        info = path.lstat()
        payload = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes() if path.is_file() else b""
        found[path.relative_to(root).as_posix()] = (info.st_dev, info.st_ino, info.st_mode,
                                                   hashlib.sha256(payload).hexdigest())
    return found


@pytest.fixture
def pending(outgoing, monkeypatch):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from tools import browser_outgoing_profile as profiles, browser_outgoing_state as state
    from tools.browser_tool_lifecycle import cleanup_browser
    from tests.tools.test_browser_outgoing_inventory import _interrupt

    first = start_session_authority(outgoing.home, "proof-original")
    sibling = start_session_authority(outgoing.home, "proof-reader")
    with scoped_current_session_id(first.session_id):
        profile = outgoing.start("proof-original")
        (profile / "Cookies").write_bytes(os.urandom(32))
        cache = profile / "Default/Cache"
        cache.mkdir(parents=True)
        for index in range(2):
            (cache / ("cache-" + str(index))).write_bytes(os.urandom(32))
        _interrupt(monkeypatch, 1)
        with pytest.raises(RuntimeError):
            cleanup_browser("proof-original")
        lease = profiles.lookup("proof-original")
        document = state.read(lease.home, lease.digest, lease.custody)
        assert document["phase"] == "checkpointed" and len(list(cache.glob("cache-*"))) == 1
    first.close()
    try:
        with scoped_current_session_id(sibling.session_id):
            yield outgoing, sibling, profile, lease, document
    finally:
        first.close()
        sibling.close()


def _replace_proof(outgoing, lease, document):
    from hermes_security import browser_profile_archive as archive
    from hermes_security.session_custody import SessionCustody
    from tools import browser_outgoing_inventory as inventory

    proof_path = lease.home / document["inventory"]["relative"]
    replacement = outgoing.ram / "replacement-proof"
    replacement.mkdir(mode=0o700)
    fresh = SessionCustody.capture(lease.home)
    archive.replay_archive(lease.home, document["inventory"]["relative"], custody=fresh,
                           slot=lease.slot + inventory._SLOT, digest=lease.digest, target=replacement)
    old_cipher = proof_path.read_bytes()
    (outgoing.ram / "original-cipher-retained").write_bytes(old_cipher)
    archive.write_archive(lease.home, document["inventory"]["relative"], replacement, custody=fresh,
                          slot=lease.slot + inventory._SLOT, identity={"kind": "same-content-replacement"},
                          digest=lease.digest)
    assert proof_path.read_bytes() != old_cipher


def _old_reference(lease, document):
    from hermes_security.session_custody import SessionCustody
    from tools import browser_outgoing_state as state

    document.pop("inventory")
    state.write(lease.home, lease.digest, SessionCustody.capture(lease.home), document)


@pytest.mark.parametrize("case", ["new-cache", "changed-cache", "new-socket", "missing-proof", "replaced-proof", "old-reference"])
def test_uncertain_native_survivors_keep_target_checkpoint_reference_and_sibling(pending, monkeypatch, case):
    from hermes_security.session_custody import SessionCustody
    from tools import browser_outgoing_recovery as recovery, browser_outgoing_state as state

    outgoing, sibling, profile, lease, document = pending
    proof_path = lease.home / document["inventory"]["relative"]
    resources = ExitStack()

    def new_socket():
        monkeypatch.chdir(profile)
        endpoint = resources.enter_context(socket.socket(socket.AF_UNIX))
        endpoint.bind("unknown-new.sock")

    changes = {
        "new-cache": lambda: (profile / "Default/Cache/new").write_bytes(os.urandom(32)),
        "changed-cache": lambda: next((profile / "Default/Cache").glob("cache-*")).write_bytes(os.urandom(32)),
        "new-socket": new_socket,
        "missing-proof": lambda: proof_path.rename(outgoing.ram / "original-cipher-retained"),
        "old-reference": lambda: _old_reference(lease, document),
        "replaced-proof": lambda: _replace_proof(outgoing, lease, document),
    }
    changes[case]()
    before = _snapshot(profile)
    checkpoint = (lease.home / lease.relative).read_bytes()
    reference = (lease.home / state.relative(lease.digest)).read_bytes()
    proof = proof_path.read_bytes() if proof_path.exists() else None
    try:
        result = recovery.recover(lease.home)
        assert result["saved"] == [] and result["retained"]
        assert recovery.recover(lease.home) == result
        assert _snapshot(profile) == before
        assert (lease.home / lease.relative).read_bytes() == checkpoint
        assert (lease.home / state.relative(lease.digest)).read_bytes() == reference
        assert (proof_path.read_bytes() if proof_path.exists() else None) == proof
        assert sibling.is_live()
        assert len(SessionCustody.capture(lease.home).derive_key("sibling-positive-control")) == 32
    finally:
        resources.close()
