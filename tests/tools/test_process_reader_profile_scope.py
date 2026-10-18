"""A process reader retains its producer's profile even for env-only launchers."""

import secrets
import threading


def test_env_only_reader_cannot_redirect_receipt_to_later_profile(tmp_path, monkeypatch):
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
    from hermes_security import io as state_io, vault
    from tools.process_registry import ProcessRegistry, ProcessSession
    from tools.process_registry_results import save_completed_result

    homes = [tmp_path / name for name in ("producer", "other")]
    for home in homes:
        password = secrets.token_urlsafe(24)
        vault.init_vault(home, password)
        vault.unlock(home, password)
    monkeypatch.setenv("HERMES_HOME", str(homes[0]))
    token = set_hermes_home_override(None)
    ready, release = threading.Event(), threading.Event()
    observed, errors = [], []
    session = ProcessSession(id="proc_profile_reader", command="echo reader", exited=True,
                             exit_code=7, output_buffer="PROFILE_OUTPUT_ORACLE")
    registry = ProcessRegistry()

    def reader(_session):
        ready.set()
        try:
            assert release.wait(5)
            observed.append(get_hermes_home())
            save_completed_result(session)
        except BaseException as exc:
            errors.append(exc)

    try:
        registry._track_started(session, reader, "profile-reader-contract")
        assert ready.wait(5)
        monkeypatch.setenv("HERMES_HOME", str(homes[1]))
        release.set()
        session._reader_thread.join(timeout=5)
        assert not session._reader_thread.is_alive()
        assert errors == []
        assert observed == [homes[0]]
        relative = "logs/process-results/proc_profile_reader.json"
        assert not (homes[1] / relative).exists()
        target = homes[0] / relative
        assert b"PROFILE_OUTPUT_ORACLE" not in target.read_bytes()
        assert state_io.read_json(target, purpose="state")["output"] == "PROFILE_OUTPUT_ORACLE"
    finally:
        release.set()
        if session._reader_thread is not None:
            session._reader_thread.join(timeout=5)
        reset_hermes_home_override(token)
