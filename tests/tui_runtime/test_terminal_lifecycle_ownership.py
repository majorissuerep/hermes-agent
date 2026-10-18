"""A terminal may finalize only a durable conversation whose ownership it can prove."""

import contextlib
import os
import subprocess
import sys
import threading

import pytest

from tests.e2e.vault_home import seal_fixture_home, spawn_unlocked
from tests.waiting import wait_until


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("other_owner", ["live_child", "corrupt_registry"])
def test_terminal_close_preserves_unknown_or_live_sibling_owner(tmp_path, monkeypatch, other_owner):
    from hermes_security.io import write_text
    from hermes_state import SessionDB
    from tui_runtime import server

    home = tmp_path / "native-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    seal_fixture_home(home)
    key = "native-owned-conversation"
    ready, release = tmp_path / "ready", tmp_path / "release"
    child = None
    with SessionDB(home / "state.db") as db:
        db.create_session(key, source="tui")
        monkeypatch.setattr(server, "_session_db", lambda _session: contextlib.nullcontext(db))
        monkeypatch.setattr(server, "_notify_session_boundary", lambda *_a, **_kw: [])
        registry = home / "runtime" / "active_sessions.json"
        if other_owner == "live_child":
            script = (
                "from hermes_security.handoff import adopt_inherited_key\n"
                "assert adopt_inherited_key()\n"
                "from hermes_cli.active_sessions import try_acquire_active_session\n"
                "from pathlib import Path\nimport sys\n"
                "lease, error = try_acquire_active_session(session_id=sys.argv[1], surface='tui', "
                "config={}, track_liveness=True)\n"
                "assert lease is not None and error is None, error\n"
                "Path(sys.argv[2]).touch()\n"
                "from tests.waiting import wait_until\n"
                "try: wait_until(lambda: Path(sys.argv[3]).exists(), timeout=30, what='release')\n"
                "finally: lease.release()\n")
            child = spawn_unlocked([sys.executable, "-c", script, key, str(ready), str(release)],
                env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            wait_until(lambda: ready.exists() or child.poll() is not None, timeout=15, what="real sibling lease")
            assert ready.exists(), child.communicate(timeout=5)
        else:
            registry.parent.mkdir(parents=True, exist_ok=True)
            write_text(registry, "{not-json", purpose="state")
        original = registry.read_bytes()
        session = {"agent": None, "history": [], "history_lock": threading.Lock(),
                   "session_key": key, "profile_home": str(home), "source": "tui"}
        try:
            server._finalize_session(session, end_reason="tui_close")
            assert db.get_session(key)["ended_at"] is None
            if child is None:
                assert registry.read_bytes() == original
            else:
                from hermes_cli.active_sessions import active_session_registry_snapshot

                owners = active_session_registry_snapshot(registry_home=home)
                assert len(owners) == 1 and owners[0]["session_id"] == key
                assert owners[0]["pid"] == child.pid
        finally:
            if child is not None:
                release.touch()
                stdout, stderr = child.communicate(timeout=15)
                assert child.returncode == 0, (stdout, stderr)
        if child is not None:
            session.pop("_finalized")
            server._finalize_session(session, end_reason="tui_close")
            assert db.get_session(key)["ended_at"] is not None
