"""UTF-8 BOM tolerance for independent jobs.json readers (dump/status).

cron/jobs.load_jobs is covered in tests/cron/test_jobs.py. dump and status
each open jobs.json themselves — keep them on the same utf-8-sig dialect.
"""

from types import SimpleNamespace


def test_dump_cron_summary_accepts_utf8_bom(tmp_path):
    from hermes_cli.dump import _cron_summary

    cron = tmp_path / "cron"
    cron.mkdir()
    (cron / "jobs.json").write_bytes(
        b'\xef\xbb\xbf{"jobs": [{"id": "j1", "enabled": true},'
        b' {"id": "j2", "enabled": false}]}'
    )

    assert _cron_summary(tmp_path) == "1 active / 2 total"




def test_status_scheduled_jobs_accepts_utf8_bom(monkeypatch, capsys, tmp_path):
    """hermes status must not print '(error reading jobs file)' under BOM."""
    from hermes_cli import status as status_mod
    cron = tmp_path / "cron"
    cron.mkdir()
    (cron / "jobs.json").write_bytes(
        b'\xef\xbb\xbf{"jobs": [{"id": "j1", "enabled": true},'
        b' {"id": "j2", "enabled": true}]}'
    )

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(status_mod, "get_hermes_home", lambda: tmp_path)
    status_mod._render_cron(SimpleNamespace(config={}))
    out = capsys.readouterr().out
    assert "(error reading jobs file)" not in out
    assert "2 active, 2 total" in out
