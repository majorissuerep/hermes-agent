"""Captured callbacks retain their original backend generation, not a mutable environment object."""
import pytest

from tests.tools._protected_backend import FakeBackend
from tools import browser_terminal as terminal
from tools.environments import streams
from tools.environments.docker import DockerEnvironment


def test_real_docker_recreation_cannot_retarget_a_captured_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    env = object.__new__(DockerEnvironment)
    env._container_id, env._docker_exe, env._labels = "original-A", "/public/docker", {}
    monkeypatch.setattr(env, "_find_reusable_container", lambda *args: ("replacement-B", "running"))
    monkeypatch.setattr(env, "init_session", lambda: None)
    targets = {name: FakeBackend([("/tmp", "ramfs", "rw")]) for name in ("original-A", "replacement-B")}
    routes = []

    def run_in(owner, argv, **kwargs):
        assert owner is env
        target = streams.exec_prefix(owner, interactive=False)[-1]
        routes.append(target)
        return targets[target].run_in(targets[target], argv, **kwargs)

    monkeypatch.setattr(streams, "run_in", run_in)
    runtime = terminal.browser_runtime(env)
    # B has the same valid protected pathnames. Path existence is not generation identity.
    targets["replacement-B"].dirs.update(targets["original-A"].dirs)
    with terminal.captured_execution(tmp_path, "docker", env, runtime):
        assert terminal.browser_runtime(env) is runtime
        assert env._recreate_container()
        before = len(routes)
        with pytest.raises(RuntimeError, match="original|changed"):
            terminal.browser_runtime(env)
        assert routes[before:] == []  # do not even probe the replacement from an old callback
    # A genuinely new request can establish a new runtime without mutating the retained old one.
    replacement = terminal.browser_runtime(env)
    assert replacement is not runtime
    assert runtime.root == replacement.root
    assert streams.exec_prefix(env, interactive=False)[-1] == "replacement-B"


@pytest.mark.parametrize("change", ["directory", "epoch"])
def test_original_callback_refuses_changed_native_identity_with_same_client(tmp_path, monkeypatch, change):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    backend = FakeBackend([("/tmp", "ramfs", "rw")])
    monkeypatch.setattr(streams, "run_in", backend.run_in)
    runtime = terminal.browser_runtime(backend)
    with terminal.captured_execution(tmp_path, "ssh", backend, runtime):
        assert terminal.browser_runtime(backend) is runtime
        if change == "directory":
            backend.native_ids[runtime.captures] = "9:999"
        else:
            backend.epoch = "00000000-0000-0000-0000-000000000001:1:1:1"
        with pytest.raises(RuntimeError, match="original|changed"):
            terminal.browser_runtime(backend)
        # Even a command already constructed before replacement refuses at the effect boundary.
        refused = backend.run_in(backend, runtime.guarded(["rm", "-f", "--", runtime.captures + "/video"]))
        assert refused.returncode == 97
    replacement = terminal.browser_runtime(backend)
    assert replacement is not runtime
    assert replacement.generation != runtime.generation


@pytest.mark.parametrize("interactive", [False, True])
def test_effect_argv_uses_the_single_validated_prefix_even_if_environment_changes(monkeypatch, interactive):
    env = object.__new__(DockerEnvironment)
    env._container_id, env._docker_exe = "original-A", "/public/docker"
    expected = tuple(streams.exec_prefix(env, interactive=False))
    resolve = streams.exec_prefix
    calls = []

    def mutate_after_read(owner, **kwargs):
        prefix = resolve(owner, **kwargs)
        calls.append(prefix)
        owner._container_id = "replacement-B"
        return prefix

    monkeypatch.setattr(streams, "exec_prefix", mutate_after_read)
    argv = streams.remote_command(env, ["printf", "original"], interactive=interactive,
                                    expected_prefix=expected)
    assert len(calls) == 1
    assert argv[:len(calls[0])] == calls[0]
    assert "original-A" in calls[0] and "replacement-B" not in calls[0]
    with pytest.raises(RuntimeError, match="changed; no command was sent"):
        streams.remote_command(env, ["rm", "capture"], interactive=interactive, expected_prefix=expected)


@pytest.mark.parametrize("backend", ["ssh", "singularity"])
def test_non_docker_target_prefix_keeps_its_native_conventions_and_refuses_change(monkeypatch, backend):
    if backend == "ssh":
        from tools.environments.ssh import SSHEnvironment
        env = object.__new__(SSHEnvironment)
        selected = ["ssh", "-p", "2200", "account@original-host"]
        monkeypatch.setattr(env, "_build_ssh_command", lambda: list(selected))
    else:
        from tools.environments.singularity import SingularityEnvironment
        env = object.__new__(SingularityEnvironment)
        env._instance_started, env.instance_id, env.executable = True, "original-instance", "/public/apptainer"
        selected = [env.executable, "exec", "instance://original-instance"]
    expected = tuple(streams.exec_prefix(env, interactive=False))
    argv = streams.remote_command(env, ["printf", "original"], interactive=True, expected_prefix=expected)
    assert argv[:len(selected)] == selected
    if backend == "ssh":
        selected[-1] = "account@replacement-host"
    else:
        env.instance_id = "replacement-instance"
    with pytest.raises(RuntimeError, match="changed; no command was sent"):
        streams.remote_command(env, ["rm", "capture"], expected_prefix=expected)
