"""Real backend classes and lifecycle with intercepted commands; no backend/app is launched."""
import gc
from pathlib import Path
import subprocess
import threading
import weakref

import pytest


@pytest.fixture(params=["docker", "singularity"])
def backend(request, monkeypatch, tmp_path):
    from tools.environments import docker, singularity

    kind, effects = request.param, []

    def run(argv, **kwargs):
        effects.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(docker.subprocess, "run", run)
    monkeypatch.setattr(singularity, "run_capture", run)

    def make():
        if kind == "docker":
            owner = object.__new__(docker.DockerEnvironment)
            owner.__dict__.update(_container_id="current", _docker_exe="unit-docker", _image="original-image",
                                  _persistent=False, _persist_across_processes=False,
                                  _workspace_dir=None, _home_dir=None, _cleanup_thread=None)
        else:
            owner = object.__new__(singularity.SingularityEnvironment)
            owner.__dict__.update(instance_id="current", executable="unit-apptainer", image=Path("original.sif"),
                                  _instance_started=True, _persistent=False, _overlay_dir=None)
        owner._hermes_browser_owner_home = tmp_path / "original-home"
        owner._hermes_browser_profile_lock = threading.RLock()
        return owner

    def drain():
        assert docker.DockerEnvironment.wait_for_all_teardowns(timeout=15)

    return kind, make, effects, drain


@pytest.mark.parametrize("outcome", ["success", "refusal", "exception"])
def test_recovery_view_collection_never_takes_original_owner_cleanup(backend, outcome):
    from tools import browser_outgoing_recovery as recovery
    from tools.environments import streams

    kind, make, effects, drain = backend
    owner = make()
    reference = {"backend": kind, "origin": {"container": "original", "instance": "original"}}

    def use_view():
        view = recovery._captured_env(reference, owner)
        prefix = streams.exec_prefix(view, interactive=False)
        assert prefix[-1] == ("original" if kind == "docker" else "instance://original")
        assert streams.remote_command(view, ["true"], expected_prefix=prefix) is not None
        if outcome == "refusal":
            raise ValueError("unchanged target retained")
        if outcome == "exception":
            raise RuntimeError("publication interrupted")

    try:
        if outcome == "success":
            use_view()
        else:
            with pytest.raises((ValueError, RuntimeError)):
                use_view()
        gc.collect()
        drain()
        assert effects == [], "A non-owning recovery view retired an original backend"
    finally:
        owner.cleanup()
        drain()


def test_original_target_and_selection_stay_pinned_but_real_owner_cleanup_still_works(backend):
    from tools import browser_outgoing_recovery as recovery
    from tools.environments import streams

    kind, make, effects, drain = backend
    owner = make()
    view = recovery._captured_env({"backend": kind, "origin": {"container": "original", "instance": "original"}}, owner)
    prefix = streams.exec_prefix(view, interactive=False)
    owner._hermes_browser_owner_home = "different-home"
    if kind == "docker":
        owner._container_id, owner._docker_exe, owner._image = "replacement", "different-client", "different-image"
        assert view._image == "original-image"
    else:
        owner.instance_id, owner.executable, owner.image = "replacement", "different-client", Path("different.sif")
        assert view.image == "original.sif"
    assert streams.exec_prefix(view, interactive=False) == prefix
    assert view._hermes_browser_profile_lock is owner._hermes_browser_profile_lock
    assert str(view._hermes_browser_owner_home).endswith("original-home")
    assert streams.remote_command(view, ["true"], expected_prefix=prefix) is not None
    with pytest.raises(RuntimeError, match="target changed"):
        streams.remote_command(view, ["true"], expected_prefix=streams.exec_prefix(owner, interactive=False))
    for attribute in ("cleanup", "execute", "_start_instance", "_recreate_container"):
        with pytest.raises(AttributeError):
            getattr(view, attribute)
    del view
    gc.collect()
    drain()
    assert effects == []
    owner.cleanup()
    drain()
    assert effects and all("replacement" in argv for argv in effects)
    assert all("original" not in argv for argv in effects)


def test_view_keeps_real_owner_alive_until_its_last_reference_ends(backend):
    from tools import browser_outgoing_recovery as recovery
    from tools.environments import streams

    kind, make, effects, drain = backend
    owner = make()
    owner_ref = weakref.ref(owner)
    view = recovery._captured_env({"backend": kind, "origin": {"container": "original", "instance": "original"}}, owner)
    del owner
    gc.collect()
    drain()
    assert owner_ref() is not None and effects == []
    assert streams.exec_prefix(view, interactive=False)[-1].endswith("original")
    del view
    gc.collect()
    drain()
    assert owner_ref() is None and effects
    assert all("current" in argv for argv in effects)


@pytest.mark.parametrize("bad", ["", "\x00bad", "x" * 257])
def test_invalid_origin_cannot_construct_an_execution_view(backend, bad):
    from tools.browser_outgoing_recovery import _captured_env

    kind, make, effects, drain = backend
    owner = make()
    try:
        with pytest.raises(ValueError, match="identity is missing"):
            _captured_env({"backend": kind, "origin": {"container": bad, "instance": bad}}, owner)
        assert effects == []
    finally:
        owner.cleanup()
        drain()


def test_backend_mismatch_refuses_without_lifecycle_and_ssh_identity_is_unchanged(backend):
    from tools.browser_outgoing_recovery import _captured_env

    kind, make, effects, drain = backend
    owner = make()
    other = "singularity" if kind == "docker" else "docker"
    try:
        with pytest.raises(ValueError, match="differs"):
            _captured_env({"backend": other, "origin": {"container": "original", "instance": "original"}}, owner)
        assert _captured_env({"backend": "ssh"}, owner) is owner
        assert effects == []
    finally:
        owner.cleanup()
        drain()
