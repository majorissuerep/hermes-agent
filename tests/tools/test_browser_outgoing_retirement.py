"""Authenticated subset recovery with real crypto/fixed helpers and synthetic browser/RAM seams."""
from dataclasses import asdict
import hashlib
import secrets
import sys

import pytest

pytest_plugins = ["tests.tools.test_browser_outgoing_continuity"]


def _contents(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def _interrupt(monkeypatch, *, completed=False, times=1):
    from tools import browser_outgoing_state as state, browser_outgoing_transfer as transfer

    original_client, original_remove = transfer.client, state.remove
    observed = []

    def client(lease, argv, **kwargs):
        if not completed and len(argv) > 4 and argv[4] == "retire" and len(observed) < times:
            observed.append(True)
            wrapper = """import os, sys, shutil
assert shutil.rmtree.avoids_symlink_attacks
original = os.unlink
def unlink(name, *args, **kwargs):
    result = original(name, *args, **kwargs)
    if name.startswith("data-"):
        raise OSError("synthetic interruption after completed data unlink")
    return result
os.unlink = unlink
payload = sys.argv[1]
sys.argv = ["fixed-profile-helper", *sys.argv[2:]]
exec(payload, {"__name__": "__main__"})
"""
            argv = [sys.executable, "-I", "-c", wrapper, argv[3], *argv[4:]]
        return original_client(lease, argv, **kwargs)

    def remove(*args, **kwargs):
        if completed and not observed:
            observed.append(True)
            raise OSError("synthetic interruption after target retirement")
        return original_remove(*args, **kwargs)

    monkeypatch.setattr(transfer, "client", client)
    monkeypatch.setattr(state, "remove", remove)
    return observed


@pytest.fixture
def interrupted(outgoing, monkeypatch):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from tools import browser_outgoing_profile as profiles, browser_outgoing_state as state
    from tools.browser_tool_lifecycle import cleanup_browser

    owners = [start_session_authority(outgoing.home, name) for name in ("partial-A", "repair-B")]

    def create(*, completed=False, times=1):
        observed = _interrupt(monkeypatch, completed=completed, times=times)
        with scoped_current_session_id(owners[0].session_id):
            profile = outgoing.start("interrupted")
            for index in range(3):
                (profile / f"data-{index}").write_bytes(secrets.token_bytes(32))
            expected = _contents(profile)
            lease = profiles.lookup("interrupted")
            with pytest.raises((RuntimeError, OSError)):
                cleanup_browser("interrupted")
            assert observed
            reference = state.read(outgoing.home, lease.digest, lease.custody)
            assert reference["phase"] == "checkpointed"
            assert len(list(profile.glob("data-*"))) == (0 if completed else 2)
            cipher = (outgoing.home / lease.relative).read_bytes()
            owners[0].close()
        return profile, lease, expected, reference, cipher

    yield outgoing, owners[1], create
    for owner in owners:
        owner.close()


@pytest.mark.parametrize("case", ["partial", "repeated", "binding-only", "completed"])
def test_explicit_repair_finishes_only_original_checkpoint_survivors(interrupted, case):
    from agent.session_context import scoped_current_session_id
    from hermes_security import browser_profile_archive as archive
    from tools import browser_outgoing_recovery as recovery, browser_outgoing_state as state

    outgoing, sibling, create = interrupted
    profile, lease, expected, reference, cipher = create(completed=case == "completed", times=2 if case == "repeated" else 1)
    original_inode = profile.stat().st_dev, profile.stat().st_ino
    if case == "binding-only":
        for path in profile.glob("data-*"):
            path.unlink()
    with scoped_current_session_id(sibling.session_id):
        if case == "repeated":
            report = recovery.recover(outgoing.home)
            assert report["saved"] == [] and report["retained"]
            assert len(list(profile.glob("data-*"))) == 1
        report = recovery.recover(outgoing.home)
        assert report["retained"] == [] and len(report["saved"]) == 1
        assert list(profile.iterdir()) == []
        assert not (outgoing.home / state.relative(lease.digest)).exists()
        restored = outgoing.ram / "verified-recovered-checkpoint"
        restored.mkdir(mode=0o700)
        from hermes_security.session_custody import SessionCustody
        custody = SessionCustody.capture(outgoing.home)
        verified = archive.replay_archive(outgoing.home, lease.relative, custody=custody,
                                          slot=lease.slot, digest=lease.digest, target=restored)
        assert asdict(verified) == reference["recovery"]
        assert _contents(restored) == {k: v for k, v in expected.items() if k != archive.RUNTIME_BINDING_NAME}
        assert recovery.recover(outgoing.home) == {"saved": [], "retained": []}
    assert (outgoing.home / lease.relative).read_bytes() == cipher
    assert (profile.stat().st_dev, profile.stat().st_ino) == original_inode


@pytest.mark.parametrize("case", ["new", "changed", "binding-missing", "binding-replaced", "root-replaced", "cipher-replaced", "older-reference", "excluded"])
def test_uncertain_remaining_state_is_retained_with_original_recovery(interrupted, case):
    from agent.session_context import scoped_current_session_id
    from hermes_security import browser_profile_archive as archive
    from hermes_security.session_custody import SessionCustody
    from tools import browser_outgoing_recovery as recovery, browser_outgoing_state as state

    outgoing, sibling, create = interrupted
    profile, lease, _expected, reference, _cipher = create()
    changes = {
        "new": lambda: (profile / "new-private-data").write_bytes(secrets.token_bytes(32)),
        "changed": lambda: next(profile.glob("data-*")).write_bytes(secrets.token_bytes(32)),
        "binding-missing": lambda: (profile / archive.RUNTIME_BINDING_NAME).unlink(),
        "binding-replaced": lambda: (profile / archive.RUNTIME_BINDING_NAME).write_text("{}"),
        "root-replaced": lambda: (profile.rename(profile.with_name("original-retained-profile")), profile.mkdir(mode=0o700)),
        "excluded": lambda: ((profile / "Default/Cache").mkdir(parents=True),
                              (profile / "Default/Cache/sole-unknown-copy").write_bytes(secrets.token_bytes(32))),
    }
    if case in changes:
        changes[case]()
    with scoped_current_session_id(sibling.session_id):
        custody = SessionCustody.capture(outgoing.home)
        if case == "older-reference":
            reference.pop("archive_cipher_sha256", None)
            state.write(outgoing.home, lease.digest, custody, reference)
        elif case == "cipher-replaced":
            source = outgoing.ram / "same-summary-replacement"
            source.mkdir(mode=0o700)
            original = archive.replay_archive(outgoing.home, lease.relative, custody=custody,
                                              slot=lease.slot, digest=lease.digest, target=source)
            replaced = archive.write_archive(outgoing.home, lease.relative, source, custody=custody,
                                              slot=lease.slot, identity=lease.identity, digest=lease.digest)
            assert replaced == original and (outgoing.home / lease.relative).read_bytes() != _cipher
        before, cipher = _contents(profile), (outgoing.home / lease.relative).read_bytes()
        ref = (outgoing.home / state.relative(lease.digest)).read_bytes()
        report = recovery.recover(outgoing.home)
        assert not report["saved"] and report["retained"]
        assert recovery.recover(outgoing.home) == report
        assert _contents(profile) == before
        assert (outgoing.home / lease.relative).read_bytes() == cipher
        assert (outgoing.home / state.relative(lease.digest)).read_bytes() == ref
        if case in {"new", "changed", "excluded"}:
            assert lease.relative.as_posix() in report["retained"][0]
            assert lease.runtime.profile in report["retained"][0]


@pytest.mark.parametrize("case", ["cipher-after-proof", "cipher-during-hash", "parent-during-hash", "reference-before-removal", "source-ended", "capacity"])
def test_repair_rechecks_original_cipher_reference_and_authority(interrupted, monkeypatch, case):
    from agent.session_context import scoped_current_session_id
    from hermes_security import browser_profile_archive as archive
    from hermes_security.session_custody import SessionCustody
    from tools import browser_outgoing_recovery as recovery, browser_outgoing_retirement as retirement
    from tools import browser_outgoing_state as state

    outgoing, sibling, create = interrupted
    profile, lease, _expected, reference, _cipher = create()
    before = _contents(profile)
    original_subset, original_remove = retirement._subset, state.remove
    changed, armed = [], []
    original_hash = retirement._PinnedCipher._hash

    def replace_cipher():
        custody = SessionCustody.capture(outgoing.home)
        source = outgoing.ram / "late-same-checkpoint"
        source.mkdir(mode=0o700)
        archive.replay_archive(outgoing.home, lease.relative, custody=custody,
                               slot=lease.slot, digest=lease.digest, target=source)
        if case == "parent-during-hash":
            parent = (outgoing.home / lease.relative).parent
            parent.rename(parent.with_name("retained-original-browser-profile"))
            parent.mkdir(mode=0o700)
        archive.write_archive(outgoing.home, lease.relative, source, custody=custody,
                              slot=lease.slot, identity=lease.identity, digest=lease.digest)

    def digest(pinned):
        result = original_hash(pinned)
        if armed and not changed:
            changed.append(True)
            replace_cipher()
        return result

    def subset(*args):
        original_subset(*args)
        if case in {"cipher-during-hash", "parent-during-hash"}:
            armed.append(True)
            return
        changed.append(True)
        if case == "source-ended":
            sibling.close()
        elif case == "cipher-after-proof":
            replace_cipher()

    def remove(*args, **kwargs):
        changed.append(True)
        custody = SessionCustody.capture(outgoing.home)
        state.write(outgoing.home, lease.digest, custody, reference)
        return original_remove(*args, **kwargs)

    if case in {"cipher-after-proof", "cipher-during-hash", "parent-during-hash", "source-ended"}:
        monkeypatch.setattr(retirement, "_subset", subset)
        if case in {"cipher-during-hash", "parent-during-hash"}:
            monkeypatch.setattr(retirement._PinnedCipher, "_hash", digest)
    elif case == "reference-before-removal":
        monkeypatch.setattr(state, "remove", remove)
    else:
        from hermes_platform.memory_capacity import CapacityBudget
        from hermes_security import browser_profile_transport
        monkeypatch.setattr(browser_profile_transport, "capacity_budget", lambda *_a, **_kw: CapacityBudget(1, "unit exhausted budget"))
    with scoped_current_session_id(sibling.session_id):
        report = recovery.recover(outgoing.home)
    assert report["saved"] == [] and report["retained"]
    assert (outgoing.home / state.relative(lease.digest)).exists()
    if case != "capacity":
        assert changed
    if case == "reference-before-removal":
        assert list(profile.iterdir()) == []  # Saved archive remains; only final metadata cleanup refused.
    else:
        assert _contents(profile) == before
    if case not in {"cipher-after-proof", "cipher-during-hash", "parent-during-hash"}:
        assert (outgoing.home / lease.relative).read_bytes() == _cipher
