"""Blank installs reach native Ink setup without a classic prompt or consent offer."""

from __future__ import annotations

import pytest


class _Launched(Exception):
    """Raised by the stubbed ``_launch_tui`` (the real one ``exec``s and never returns)."""


def _run_chat(monkeypatch, argv, *, provider_configured):
    import hermes_cli.main as main_mod
    from hermes_cli._parser import build_top_level_parser
    import hermes_cli.observability.shared_metrics_consent as consent

    parser, _subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=main_mod.cmd_chat)
    args = parser.parse_args(argv)
    calls: list[str] = []

    def launch_tui(*_a, **_k):
        calls.append("tui")
        raise _Launched

    monkeypatch.setattr(main_mod, "_has_any_provider_configured", lambda: provider_configured)
    monkeypatch.setattr(main_mod, "_first_run_setup_guard", lambda _args: calls.append("guard"))
    monkeypatch.setattr(consent, "offer_consent_before_chat", lambda _args: calls.append("offer"))
    monkeypatch.setattr(main_mod, "_launch_tui", launch_tui)
    monkeypatch.setattr(main_mod, "_pin_kanban_board_env", lambda: None)
    monkeypatch.setattr(main_mod, "_start_chat_background_prefetch", lambda: None)
    monkeypatch.setattr(main_mod, "_confirm_startup_expensive_model_override", lambda _args: None)
    monkeypatch.setattr(main_mod, "_sync_bundled_skills_for_startup", lambda: None, raising=False)
    try:
        main_mod.cmd_chat(args)
    except _Launched:
        pass
    except SystemExit as exc:
        assert exc.code == 0, f"cmd_chat exited {exc.code} after {calls}"
    return calls


@pytest.fixture(autouse=True)
def _no_tui_env(monkeypatch):
    monkeypatch.delenv("HERMES_TUI", raising=False)


def test_blank_install_tui_skips_classic_guard_and_offer(monkeypatch):
    assert _run_chat(monkeypatch, ["chat", "--tui"], provider_configured=False) == ["tui"]


def test_blank_install_default_is_native_tui(monkeypatch):
    assert _run_chat(monkeypatch, ["chat"], provider_configured=False) == ["tui"]
