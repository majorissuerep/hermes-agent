"""Real Docker class/cleanup code, intercepted Docker commands; synthetic browser/target/RAM."""
import gc
import subprocess
import pytest

pytest_plugins = ["tests.tools.test_browser_outgoing_continuity"]
pytestmark = pytest.mark.platforms("linux")


def test_failed_recovery_view_does_not_destroy_the_original_retained_container(outgoing, monkeypatch):
    from agent.session_context import scoped_current_session_id
    from hermes_security.session_authority import start_session_authority
    from tools import browser_terminal
    from tools import browser_outgoing_profile as profiles, browser_outgoing_recovery as recovery
    from tools import browser_outgoing_state as state, browser_outgoing_transfer as transfer
    from tools.environments.docker import DockerEnvironment
    from tests.tools.test_browser_outgoing_retirement import _interrupt
    from tools.browser_tool_lifecycle import cleanup_browser

    selected = outgoing.choose("docker-synthetic-target")
    env = object.__new__(DockerEnvironment)
    env.__dict__.update(vars(selected), _image="public-unit-image", _container_id="unit-original-container",
                        _persistent=False, _persist_across_processes=False, _docker_exe="unit-docker",
                        _workspace_dir=None, _home_dir=None, _cleanup_thread=None)
    effects, real_run = [], subprocess.run

    def run(argv, **kwargs):
        if argv and argv[0] == "unit-docker":
            effects.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(browser_terminal, "terminal_backend", lambda: "docker")
    monkeypatch.setattr(browser_terminal, "terminal_environment", lambda **_kw: env)
    monkeypatch.setattr(transfer, "docker_identity", lambda _lease: "d" * 64)
    first = start_session_authority(outgoing.home, "retained-docker-A")
    fresh = start_session_authority(outgoing.home, "repair-docker-B")
    try:
        with scoped_current_session_id(first.session_id):
            profile = outgoing.start("retained-docker")
            for name in ("data-one", "data-two"):
                (profile / name).write_bytes(name.encode())
            _interrupt(monkeypatch)
            with pytest.raises(RuntimeError):
                cleanup_browser("retained-docker")
            lease = profiles.lookup("retained-docker")
            (profile / "operator-new-private-data").write_bytes(b"new unarchived synthetic data")
            before = {p.name: p.read_bytes() for p in profile.iterdir() if p.is_file()}
            reference = (outgoing.home / state.relative(lease.digest)).read_bytes()
            cipher = (outgoing.home / lease.relative).read_bytes()
        first.close()
        with scoped_current_session_id(fresh.session_id):
            report = recovery.recover(outgoing.home)
            assert report["saved"] == [] and report["retained"]
            assert {p.name: p.read_bytes() for p in profile.iterdir() if p.is_file()} == before
            assert (outgoing.home / state.relative(lease.digest)).read_bytes() == reference
            assert (outgoing.home / lease.relative).read_bytes() == cipher
        gc.collect()
        assert DockerEnvironment.wait_for_all_teardowns(timeout=15)
        assert effects == [], "A temporary non-owning recovery view invoked real Docker stop/rm cleanup"
        assert env._container_id == "unit-original-container"
    finally:
        first.close()
        fresh.close()
        env._container_id = None  # Fixture owns its synthetic backend, no real Docker operation exists.
