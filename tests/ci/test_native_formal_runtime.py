"""The required native phase carries PM ownership through the hermetic runner."""
import os

import pytest


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("profile", ["focused", "full"])
def test_native_formal_phase_forwards_exact_selected_store(monkeypatch, tmp_path, profile):
    from pm import paths
    from scripts.ci import native_checks, native_namespace_policy

    monkeypatch.setenv("HERMES_CI_TEST_PROFILE", profile)
    store = tmp_path / "verified-tools"
    store.mkdir()
    calls = []
    environments = []
    monkeypatch.setattr(paths, "store_root", lambda: store)
    monkeypatch.setattr(native_namespace_policy, "reexec_if_required", lambda: None)
    monkeypatch.setattr(native_checks, "bootstrap_protocol", lambda *_args: None)
    def record(*args, env):
        calls.append(args)
        environments.append(env)
    monkeypatch.setattr(native_checks, "run", record)
    monkeypatch.setattr(native_checks.subprocess, "check_output", lambda *_args, **_kwargs: b"")
    native_checks.main()
    install = next(i for i, args in enumerate(calls) if "scripts/ci/install_lean.py" in args)
    formal = next(i for i, args in enumerate(calls) if "--formal-checker-store" in args)
    assert install < formal
    wrapper = calls[formal]
    assert "scripts.ci.protected_management_checks" in wrapper
    assert wrapper[wrapper.index("--formal-checker-store") + 1] == os.fspath(store.resolve())
    assert wrapper[wrapper.index("--test-profile") + 1] == profile
    args = native_checks.formal_test_command(profile, store.resolve())
    assert args[0] == "scripts/run_tests.sh"
    assert args[args.index("--file-retries") + 1] == "0" and "--fail-on-skip" in args
    assert all(path in args for path in native_checks.FORMAL_FILES)
    assert args[args.index("--formal-checker-store") + 1] == os.fspath(store.resolve())
    assert ("-k" in args) is (profile == "focused")
    assert all(env["HERMES_TEST_WORKERS"] == "1" for env in environments)
    managed = next(call for call in calls if "scripts.ci.protected_management_checks" in call)
    assert managed[managed.index("--test-profile") + 1] == profile
    if profile == "focused":
        assert not any(native_checks.LINUX_FILES[0] in call for call in calls)
