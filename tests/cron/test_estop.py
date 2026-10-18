"""Global emergency stop (`hermes pause` / `hermes resume`) — agent/estop.py.

The ESTOP sentinel is a resumable pause for NEW work only: cron dispatch,
kanban dispatch, and new gateway turns are halted while it is engaged; work
already in flight is never touched. Removing the sentinel (`hermes resume`)
restores normal operation with no restart.

Ported from: gastownhall/gastown estop.go (MIT); related prior art: #26778
(/panic — kill/exit semantics, deliberately different) and #44617
(interrupt in-flight cron — deliberately NOT done here).
"""

from __future__ import annotations

import argparse
import json

import pytest

from agent import estop


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Point HERMES_HOME at a temp dir and reset estop module log state."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    estop._logged_components.clear()
    return tmp_path


# ── sentinel create / remove ────────────────────────────────────────────────


def test_engage_creates_sentinel_and_is_engaged(hermes_home):
    assert estop.is_engaged() is False
    estop.engage()
    assert (hermes_home / "ESTOP").exists()
    assert estop.is_engaged() is True


def test_disengage_removes_sentinel(hermes_home):
    estop.engage()
    assert estop.disengage() is True
    assert not (hermes_home / "ESTOP").exists()
    assert estop.is_engaged() is False
    # Disengaging when not engaged is a no-op that reports False.
    assert estop.disengage() is False


def test_reason_and_timestamp_stored(hermes_home):
    estop.engage(reason="runaway cron fan-out")
    state = estop.get_state()
    assert state is not None
    assert state["reason"] == "runaway cron fan-out"
    assert state["engaged_at"]  # ISO timestamp string

    raw = json.loads((hermes_home / "ESTOP").read_text(encoding="utf-8"))
    assert raw["reason"] == "runaway cron fan-out"




def test_corrupt_sentinel_still_engages(hermes_home):
    """A hand-touched/corrupt ESTOP file must still pause (fail safe)."""
    (hermes_home / "ESTOP").write_text("not json", encoding="utf-8")
    assert estop.is_engaged() is True
    state = estop.get_state()
    assert state is not None
    assert state.get("reason") is None


# ── paused notice for new gateway turns ─────────────────────────────────────




def test_paused_reply_surfaces_reason_and_resume_hint(hermes_home):
    estop.engage(reason="deploy window")
    notice = estop.paused_reply()
    assert notice is not None
    assert "paused" in notice.lower()
    assert "deploy window" in notice




# ── check_paused: cheap gate + log-once ─────────────────────────────────────




# ── cron scheduler integration ──────────────────────────────────────────────


def test_cron_tick_skips_dispatch_when_engaged(hermes_home, monkeypatch):
    from cron import scheduler

    calls = []

    def _fake_get_due_jobs():
        calls.append(1)
        return []

    monkeypatch.setattr(scheduler, "get_due_jobs", _fake_get_due_jobs)

    estop.engage(reason="test")
    assert scheduler.tick(verbose=False) == 0
    assert calls == [], "engaged ESTOP must skip the due-job scan entirely"


def test_cron_tick_resumes_after_disengage(hermes_home, monkeypatch):
    from cron import scheduler

    calls = []

    def _fake_get_due_jobs():
        calls.append(1)
        return []

    monkeypatch.setattr(scheduler, "get_due_jobs", _fake_get_due_jobs)

    estop.engage()
    scheduler.tick(verbose=False)
    assert calls == []

    estop.disengage()
    scheduler.tick(verbose=False)
    assert calls == [1], "resume must restore normal cron dispatch"


# ── kanban dispatcher integration ───────────────────────────────────────────




# ── gateway turn-start integration ──────────────────────────────────────────


class _FakeSource:
    platform = None
    chat_id = "c1"
    user_id = "u1"
    user_name = "user"
    chat_type = "dm"
    profile = None


class _FakeEvent:
    internal = False
    text = "hello"

    def __init__(self):
        self.source = _FakeSource()

    def get_command(self) -> str | None:
        return None






# ── CLI: hermes pause / hermes resume ───────────────────────────────────────


def test_cli_pause_engages_with_reason(hermes_home, capsys):
    from hermes_cli.subcommands.pause import cmd_pause

    rc = cmd_pause(argparse.Namespace(reason="ops incident"))
    assert rc == 0
    assert estop.is_engaged() is True
    assert estop.get_state()["reason"] == "ops incident"
    assert "paused" in capsys.readouterr().out.lower()


def test_cli_pause_idempotent(hermes_home, capsys):
    from hermes_cli.subcommands.pause import cmd_pause

    assert cmd_pause(argparse.Namespace(reason=None)) == 0
    assert cmd_pause(argparse.Namespace(reason=None)) == 0
    assert estop.is_engaged() is True


def test_cli_resume_disengages(hermes_home, capsys):
    from hermes_cli.subcommands.pause import cmd_pause, cmd_resume

    cmd_pause(argparse.Namespace(reason=None))
    rc = cmd_resume(argparse.Namespace())
    assert rc == 0
    assert estop.is_engaged() is False
    assert "resumed" in capsys.readouterr().out.lower()






# ── hermes status surfacing ─────────────────────────────────────────────────


def test_status_line_when_paused(hermes_home):
    from hermes_cli.status import _estop_status_line

    assert _estop_status_line() is None
    estop.engage(reason="ops")
    line = _estop_status_line()
    assert line is not None
    assert "paused" in line.lower()
    assert "ops" in line
    estop.disengage()
    assert _estop_status_line() is None


# ── post-merge audit fixes (#81148 follow-up) ───────────────────────────────


def test_is_engaged_fails_safe_on_stat_error(hermes_home, monkeypatch):
    """A stat failure must report ENGAGED (fail safe) — the pause has to
    hold even when HERMES_HOME is misbehaving, matching the module's
    corrupt-sentinel doctrine."""
    class _BoomPath:
        def exists(self):
            raise OSError("permission denied")

    monkeypatch.setattr(estop, "sentinel_path", lambda: _BoomPath())
    assert estop.is_engaged() is True


class _FakeCmdEvent(_FakeEvent):
    text = "/status"

    def get_command(self):
        return "status"

    def get_command_args(self):
        return ""




class _FakePauseEvent(_FakeEvent):
    def __init__(self, args=""):
        super().__init__()
        self._args = args
        self.text = f"/pause {args}".strip()

    def get_command(self):
        return "pause"

    def get_command_args(self):
        return self._args
