"""Fork regressions: clobbered sealed state must surface the vault remedy, not
raw tracebacks or file-editing advice.

Incident class (manual QA emulation, 2026-10-22): an old-venv process rewrites a
sealed file as plaintext inside a vaulted home. Readers correctly refuse the
plaintext (``PlaintextStateError``), but the CLI surfaces were hostile:
``cron list`` dumped a full traceback ("Cron database corrupted and
unrepairable"), and the config load path advised ``hermes config edit`` — which
would hand-edit ciphertext. The remedy is ``hermes secure-vault repair``.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from hermes_security import migrate as mig
from hermes_security import vault as hv

PW = "clobber-surface-pw"


@pytest.fixture()
def clobbered_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    (home / "cron").mkdir(parents=True)
    (home / "cron" / "jobs.json").write_text(
        '{"jobs": [], "updated_at": "2026-10-22T00:00:00"}', encoding="utf-8"
    )
    (home / "config.yaml").write_text("model:\n  provider: auto\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mig.migrate_home(home, PW).ok
    hv.clear_vault_cache()
    hv.unlock(home, PW)
    # Old-venv clobber: plaintext rewrite of sealed state.
    (home / "cron" / "jobs.json").write_text("[]\n", encoding="utf-8")
    (home / "config.yaml").write_text("model:\n  provider: auto\n", encoding="utf-8")
    yield home
    hv.clear_vault_cache()


def test_load_jobs_clobbered_error_names_the_repair_command(clobbered_home, capsys):
    from cron import jobs as cron_jobs

    with pytest.raises(RuntimeError) as excinfo:
        cron_jobs.load_jobs()
    message = str(excinfo.value)
    assert "secure-vault repair" in message, message
    # Not the old hostile surface.
    assert "unrepairable" not in message


def test_cron_command_clobbered_prints_remedy_not_traceback(clobbered_home, capsys, monkeypatch):
    """The CLI dispatch entry must print one actionable line and return 1, never
    let a raw ``PlaintextStateError``-carrying RuntimeError traceback escape."""
    from hermes_cli import cron as cron_cli

    class _Args:
        cron_command = "list"
        all = False

    rc = cron_cli.cron_command(_Args())
    assert rc == 1
    out = capsys.readouterr()
    combined = (out.out + out.err)
    assert "secure-vault repair" in combined
    assert "Traceback" not in combined


def test_config_parse_failure_on_plaintext_state_advises_repair(clobbered_home):
    """The config failure funnel must classify ``PlaintextStateError`` as
    clobbered state (remedy: repair), distinct from YAML damage (remedy: edit)."""
    from hermes_cli.config_read_errors import format_config_parse_failure
    from hermes_security.errors import PlaintextStateError

    exc = PlaintextStateError("/x/config.yaml is plaintext inside a vaulted home")
    msg = format_config_parse_failure(Path("/x/config.yaml"), exc)
    assert "secure-vault repair" in msg
    assert "config edit" not in msg


def test_config_parse_failure_on_yaml_damage_still_advises_edit(clobbered_home):
    from hermes_cli.config_read_errors import format_config_parse_failure

    class _Mark:
        line = 3

    class _YamlError(ValueError):
        problem = "did not find expected key"
        problem_mark = _Mark()

    msg = format_config_parse_failure(Path("/x/config.yaml"), _YamlError())
    assert "config edit" in msg
    assert "secure-vault repair" not in msg
