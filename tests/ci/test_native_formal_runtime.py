"""The required native phase carries PM ownership through the hermetic runner."""
import os

import pytest


@pytest.mark.platforms("linux")
def test_native_formal_phase_forwards_exact_selected_store(monkeypatch, tmp_path):
    from pm import paths
    from scripts.ci import native_checks, native_namespace_policy

    store = tmp_path / "verified-tools"
    store.mkdir()
    calls = []
    monkeypatch.setattr(paths, "store_root", lambda: store)
    monkeypatch.setattr(native_namespace_policy, "reexec_if_required", lambda: None)
    monkeypatch.setattr(native_checks, "bootstrap_protocol", lambda *_args: None)
    monkeypatch.setattr(native_checks, "run", lambda *args, env: calls.append(args))
    monkeypatch.setattr(native_checks.subprocess, "check_output", lambda *_args, **_kwargs: b"")
    native_checks.main()
    install = next(i for i, args in enumerate(calls) if "scripts/ci/install_lean.py" in args)
    formal = next(i for i, args in enumerate(calls) if native_checks.FORMAL_FILES[0] in args)
    assert install < formal
    args = calls[formal]
    assert args[0] == "scripts/run_tests.sh"
    assert args[args.index("--file-retries") + 1] == "0" and "--fail-on-skip" in args
    assert all(path in args for path in native_checks.FORMAL_FILES)
    assert args[args.index("--formal-checker-store") + 1] == os.fspath(store.resolve())
