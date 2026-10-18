"""Update retries use the same dependency transaction as a newly pulled tree."""

import subprocess
from types import SimpleNamespace

import pytest

import pm
from hermes_cli import main, terminal_update






@pytest.mark.parametrize("failure,exception,code", [
    (None, None, 0), (SystemExit(3), SystemExit, 3),
    (SystemExit(None), SystemExit, 1), (RuntimeError("tail exploded"), RuntimeError, 1),
    (pm.InstallError("venv", "conflict"), SystemExit, 1),
    (subprocess.CalledProcessError(23, ["python", "-m", "hermes_cli.source_build"]), SystemExit, 1),
])
@pytest.mark.parametrize("reexec", [False, True])
def test_command_reports_outcome_and_releases_real_lock(tmp_path, monkeypatch, failure, exception, code, reexec):
    from hermes_cli import update_lock, update_receipt

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("hermes_cli.update_owning_install.retarget_to_owning_install", lambda root: None)
    monkeypatch.setenv("HERMES_UPDATE_REEXEC", "1" if reexec else "")
    monkeypatch.setattr("os._exit", lambda *_: pytest.fail("normal cleanup bypassed"))
    monkeypatch.setattr(main, "_update_preflight_handled", lambda args: False)
    monkeypatch.setattr(main, "_install_hangup_protection", lambda **kw: None)
    finalized = []
    monkeypatch.setattr(main, "_finalize_update_output", finalized.append)

    def fail(args):
        assert update_lock.update_marker_path().is_file()
        update_receipt.begin_update_receipt()
        if failure is not None:
            raise failure

    monkeypatch.setattr(terminal_update, "update_source", fail)
    if exception is None:
        main.cmd_update(SimpleNamespace())
    else:
        with pytest.raises(exception) as error:
            main.cmd_update(SimpleNamespace())
        if exception is SystemExit:
            assert error.value.code == (failure.code if isinstance(failure, SystemExit) else code)
    receipt = update_receipt.read_latest_receipt()
    assert receipt is not None
    assert receipt["exit_code"] == code
    assert receipt["outcome"] == ("failed" if code else "success")
    assert finalized == [None]
    lock = update_lock.UpdateLock(install_root=tmp_path)
    assert lock.acquire()
    lock.release()
