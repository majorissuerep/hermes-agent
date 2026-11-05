"""Original excluded-state recovery with real crypto/fixed helpers and synthetic RAM/browser seams."""
import hashlib
import os
import sys

import pytest

pytest_plugins = ["tests.tools.test_browser_outgoing_continuity"]
pytestmark = pytest.mark.platforms("linux")


def _interrupt(monkeypatch, times):
    from tools import browser_outgoing_transfer as transfer

    original, observed = transfer.client, []

    def client(lease, argv, **kwargs):
        if len(observed) < times and len(argv) > 4 and argv[4] == "retire":
            observed.append(True)
            wrapper = r'''
import os,sys,shutil
assert shutil.rmtree.avoids_symlink_attacks
original=os.unlink
def unlink(name,*args,**kwargs):
    result=original(name,*args,**kwargs)
    if name.startswith("cache-"):
        raise OSError("synthetic interruption after completed original cache unlink")
    return result
os.unlink=unlink
program=sys.argv[1];sys.argv=["fixed-helper",*sys.argv[2:]]
exec(program,{"__name__":"__main__"})
'''
            argv = [sys.executable, "-I", "-c", wrapper, argv[3], *argv[4:]]
        return original(lease, argv, **kwargs)

    monkeypatch.setattr(transfer, "client", client)
    return observed


@pytest.mark.parametrize("repeat", [False, True])
def test_fresh_repair_finishes_unchanged_original_cache_and_runtime_survivors(outgoing, monkeypatch, repeat):
    from agent.session_context import scoped_current_session_id
    from hermes_security import browser_profile_archive as archive
    from hermes_security.session_authority import start_session_authority
    from tools import browser_outgoing_profile as profiles, browser_outgoing_recovery as recovery
    from tools import browser_outgoing_state as state
    from tools.browser_tool_lifecycle import cleanup_browser

    first = start_session_authority(outgoing.home, "native-original")
    sibling = start_session_authority(outgoing.home, "native-repair")
    try:
        observed = _interrupt(monkeypatch, 2 if repeat else 1)
        with scoped_current_session_id(first.session_id):
            profile = outgoing.start("native-original")
            body = os.urandom(32)
            (profile / "Cookies").write_bytes(body)
            cache = profile / "Default/Cache"
            cache.mkdir(parents=True)
            for index in range(2):
                (cache / ("cache-" + str(index))).write_bytes(os.urandom(32))
            (profile / "SingletonSocket").symlink_to("/original-dead-runtime-socket")
            lease = profiles.lookup("native-original")
            with pytest.raises(RuntimeError):
                cleanup_browser("native-original")
            assert observed == [True]
            assert len(list(cache.glob("cache-*"))) == 1
            reference = state.read(outgoing.home, lease.digest, lease.custody)
            assert reference["phase"] == "checkpointed"
            cipher = (outgoing.home / lease.relative).read_bytes()
        first.close()
        with scoped_current_session_id(sibling.session_id):
            if repeat:
                refused = recovery.recover(outgoing.home)
                assert refused["saved"] == [] and refused["retained"]
                assert len(list(cache.glob("cache-*"))) == 0
            result = recovery.recover(outgoing.home)
            assert result["retained"] == [] and len(result["saved"]) == 1
            assert list(profile.iterdir()) == []
            assert not (outgoing.home / state.relative(lease.digest)).exists()
            assert (outgoing.home / lease.relative).read_bytes() == cipher
            assert archive.archive_paths(outgoing.home) == [outgoing.home / lease.relative]
            outgoing.choose("after-native-repair")
            restored = outgoing.start("after-native-repair")
            assert (restored / "Cookies").read_bytes() == body
            assert not (restored / "Default/Cache").exists()
            assert not (restored / "inventory.json").exists()
            cleanup_browser("after-native-repair")
    finally:
        first.close()
        sibling.close()
