"""Migrated shell/Python scripts execute from a private decrypted sibling view."""
from pathlib import Path

import pytest


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("kind", ["py", "sh"])
def test_migrated_script_keeps_siblings_and_explicit_workdir(tmp_path, monkeypatch, kind):
    from cron import scheduler
    from cron.scheduler_script import _run_job_script
    from hermes_security.migrate import migrate_home

    home, work = tmp_path / "profile", tmp_path / "project"
    scripts = home / "scripts"
    scripts.mkdir(parents=True)
    work.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(scheduler, "_get_hermes_home", lambda: home)
    (scripts / "data.txt").write_text("sibling-secret-unique")
    (scripts / "helper.py").write_text("VALUE = 'helper-value:'\n")
    source = scripts / f"main.{kind}"
    if kind == "py":
        source.write_text("from pathlib import Path\nfrom helper import VALUE\nprint(VALUE + Path(__file__).with_name('data.txt').read_text())\nprint(Path.cwd())\nprint(__file__)\n")
    else:
        source.write_text('#!/bin/sh\ncat "$(dirname "$0")/data.txt"\nprintf "\\n%s\\n%s\\n" "$PWD" "$0"\n')
    report = migrate_home(home, "synthetic-script-migration-password")
    assert not report.failures
    encrypted = source.read_bytes()
    assert encrypted.startswith(b"HRMVAULT\x00")
    success, output = _run_job_script(source.name, workdir=str(work))
    assert success, output
    assert "sibling-secret-unique" in output
    assert str(work) in output
    execution = Path(output.strip().splitlines()[-1])
    assert execution != source and not execution.exists()
    assert source.read_bytes() == encrypted
    success, output = _run_job_script(source.name)
    assert success and "sibling-secret-unique" in output
    assert not Path(output.strip().splitlines()[-1]).exists()


def test_plaintext_in_vault_is_refused_and_scratch_is_clean(tmp_path, monkeypatch):
    from cron import scheduler
    from cron.scheduler_script import _run_job_script
    from hermes_security import vault

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(scheduler, "_get_hermes_home", lambda: home)
    vault.init_vault(home, "synthetic-script-password")
    vault.unlock(home, "synthetic-script-password")
    (home / "scripts").mkdir()
    (home / "scripts" / "plain.py").write_text("print('must-not-run')")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(scratch))
    success, error = _run_job_script("plain.py")
    assert not success and "plaintext" in error.lower()
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("exit_kind", ["failure", "timeout"])
def test_execution_view_is_removed_after_failed_or_timed_out_script(tmp_path, monkeypatch, exit_kind):
    from cron import scheduler
    from cron.scheduler_script import _run_job_script
    from hermes_security import vault
    from hermes_security.io import write_text

    home, scratch = tmp_path / "profile", tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(scheduler, "_get_hermes_home", lambda: home)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(scratch))
    vault.init_vault(home, "synthetic-failed-script-password")
    vault.unlock(home, "synthetic-failed-script-password")
    scripts = home / "scripts"
    scripts.mkdir()
    body = "raise RuntimeError('script failure sentinel')" if exit_kind == "failure" else "import threading; threading.Event().wait()"
    write_text(scripts / "main.py", body, purpose="state")
    monkeypatch.setattr("cron.scheduler_script._get_script_timeout", lambda: 0.05 if exit_kind == "timeout" else 10)
    success, error = _run_job_script("main.py")
    assert not success
    assert ("timed out" if exit_kind == "timeout" else "script failure sentinel") in error
    assert not list(scratch.iterdir())
