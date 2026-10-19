"""A pytest test-boundary home reset cannot redirect an E2E PM preparation."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event

import pytest


@pytest.mark.parametrize("tui", [False, True])
def test_fixture_pm_uses_immutable_child_home_during_parent_reset(tmp_path, monkeypatch, tui):
    from tests.e2e import vault_home
    import pm

    target = tmp_path / "owner"
    unrelated = tmp_path / "next-pytest-home"
    started, changed = Event(), Event()
    observed = []
    monkeypatch.setattr(vault_home, "seal_fixture_home", lambda home: None)

    def reset_parent():
        assert started.wait(5)
        monkeypatch.setenv("HERMES_HOME", str(unrelated))
        changed.set()

    def observe(environment):
        started.set()
        assert changed.wait(5)
        observed.append(environment["HERMES_HOME"])
        assert environment["HERMES_HOME"] == str(target)

    def old_in_process_sync(**kwargs):
        observe(os.environ)

    class Prepared:
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def communicate(self, timeout):
            return "", None

    def child(argv, *, env, **kwargs):
        observe(env)
        assert kwargs["cwd"] == tmp_path
        assert str(target) in argv
        return Prepared()

    monkeypatch.setattr(pm, "sync_venv", old_in_process_sync)
    monkeypatch.setattr(vault_home, "spawn_unlocked", child)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reset = pool.submit(reset_parent)
        prepare = vault_home.prepare_fixture_tui if tui else vault_home.prepare_fixture_runtime
        prepare(target, tmp_path)
        reset.result(timeout=5)
    assert observed == [str(target)]
    assert os.environ["HERMES_HOME"] == str(unrelated)
