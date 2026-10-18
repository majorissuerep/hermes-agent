"""Native side-agent threads inherit context and bind their owning profile secrets."""

import contextvars
from pathlib import Path
import queue
import threading


def test_side_workers_preserve_context_and_profile_scope(tmp_path, monkeypatch):
    from hermes_constants import get_hermes_home
    from agent.secret_scope import get_secret, set_multiplex_active
    from tui_runtime import server

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    homes = [tmp_path / "a", tmp_path / "b"]
    for home in homes:
        home.mkdir()
        (home / "config.yaml").write_text("terminal:\n  backend: local\n")
        (home / ".env").write_text(f"RETIREMENT_SCOPE={home.name}\n")
    marker = contextvars.ContextVar("retirement_test_marker", default="missing")
    marker.set("inherited")
    emitted = queue.Queue()
    monkeypatch.setattr(server, "_emit", lambda *a: emitted.put(a))
    set_multiplex_active(True)
    try:
        for home in [homes[0], homes[1], homes[0]]:
            entered, release = (threading.Event() for _ in range(2))
            observed = []

            def body():
                observed.append((get_hermes_home(), get_secret("RETIREMENT_SCOPE"), marker.get()))
                entered.set()
                assert release.wait(10)
                return "finished"

            session = {"profile_home": str(home), "cwd": str(tmp_path)}
            try:
                result = server._spawn_side_agent("r", session, "side", "parent", "background.complete", body)
                assert result["result"]["task_id"] == "side"
                assert entered.wait(10)
                assert observed == [(home, home.name, "inherited")]
                release.set()
            finally:
                release.set()
                for thread in threading.enumerate():
                    if thread.name == "side-agent-side":
                        thread.join(10)
            emitted.get(timeout=10)
    finally:
        set_multiplex_active(False)
