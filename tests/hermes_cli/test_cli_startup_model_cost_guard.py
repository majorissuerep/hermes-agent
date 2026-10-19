from argparse import Namespace
import sys

import pytest


class _NonInteractiveStdin:
    def isatty(self):
        return False


def _chat_args(**overrides):
    base = {
        "continue_last": None,
        "model": None,
        "provider": None,
        "resume": None,
        "no_restore_cwd": False,
        "toolsets": None,
        "skills": None,
        "tui": False,
        "tui_dev": False,
        "verbose": None,
        "quiet": True,
        "query": "hello",
        "image": None,
        "worktree": False,
        "checkpoints": False,
        "pass_session_id": False,
        "max_turns": None,
        "ignore_rules": False,
        "ignore_user_config": False,
        "safe_mode": False,
        "compact": False,
        "source": None,
        "yolo": False,
        "accept_hooks": False,
    }
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture
def main_mod(monkeypatch, tmp_path):
    import hermes_cli.main as mod

    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_sweep_stale_bytecode_if_checkout_changed", lambda: None)
    monkeypatch.setattr("hermes_cli.boot_bootstrap.maybe_run_boot_bootstrap", lambda _root: None)
    monkeypatch.setattr(mod, "_has_any_provider_configured", lambda: True)
    monkeypatch.setattr(mod, "_sync_bundled_skills_for_startup", lambda: None)
    monkeypatch.setattr(mod, "_pin_kanban_board_env", lambda: None)
    monkeypatch.setattr(mod, "_resolve_session_by_name_or_id", lambda val: val)
    monkeypatch.setattr(mod, "_oneshot_cleanup_done", False)
    return mod


@pytest.fixture
def headless_calls(monkeypatch):
    captured = {}

    def capture_run(_prompt, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("hermes_cli.main._run_and_exit_oneshot", capture_run)
    return captured


@pytest.fixture
def codex_config(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {
            "model": {
                "provider": "openai-codex",
                "default": "gpt-5.5",
                "base_url": "https://chatgpt.com/backend-api/codex",
            }
        },
    )


def _set_startup_config(
    monkeypatch, *, provider="custom", default="safe-model", base_url="", ack=None
):
    model = {"provider": provider, "default": default}
    if base_url:
        model["base_url"] = base_url
    config = {"model": model}
    if ack is not None:
        config["security"] = {
            "allow_data_training_tiers_noninteractive": ack,
        }
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: config)


def test_cmd_chat_rejects_noninteractive_gpt55_pro_startup_override(
    main_mod, headless_calls, codex_config, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(_chat_args(model="openai/gpt-5.5-pro"))

    assert excinfo.value.code == 1
    assert not headless_calls
    err = capsys.readouterr().err
    assert "EXPENSIVE MODEL WARNING" in err
    assert "non-interactive" in err


def test_cmd_chat_rejects_noninteractive_gpt55_pro_even_with_yolo(
    main_mod, headless_calls, codex_config, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(_chat_args(model="openai/gpt-5.5-pro", yolo=True))

    assert excinfo.value.code == 1
    assert not headless_calls
    assert "EXPENSIVE MODEL WARNING" in capsys.readouterr().err


def test_cmd_chat_allows_acknowledged_data_training_tier_noninteractively(
    main_mod, headless_calls, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    _set_startup_config(monkeypatch, ack=True)

    main_mod.cmd_chat(
        _chat_args(model="muse-spark-1.2-contributor", provider="custom")
    )

    assert headless_calls["model"] == "muse-spark-1.2-contributor"
    err = capsys.readouterr().err
    assert "TRAINS ON YOUR DATA" in err
    assert "security.allow_data_training_tiers_noninteractive" in err


def test_cmd_chat_rejects_unacknowledged_data_training_tier_with_opt_in_hint(
    main_mod, headless_calls, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    _set_startup_config(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(
            _chat_args(model="muse-spark-1.2-contributor", provider="custom")
        )

    assert excinfo.value.code == 1
    assert not headless_calls
    err = capsys.readouterr().err
    assert "TRAINS ON YOUR DATA" in err
    assert "security.allow_data_training_tiers_noninteractive" in err


def test_data_training_acknowledgement_does_not_bypass_cost_guard(
    main_mod, headless_calls, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    _set_startup_config(
        monkeypatch,
        provider="openai-codex",
        default="gpt-5.5",
        base_url="https://chatgpt.com/backend-api/codex",
        ack=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(_chat_args(model="openai/gpt-5.5-pro"))

    assert excinfo.value.code == 1
    assert not headless_calls
    assert "EXPENSIVE MODEL WARNING" in capsys.readouterr().err


def test_data_training_acknowledgement_requires_literal_true(
    main_mod, headless_calls, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    _set_startup_config(monkeypatch, ack="true")

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(
            _chat_args(model="muse-spark-1.2-contributor", provider="custom")
        )

    assert excinfo.value.code == 1
    assert not headless_calls


def test_data_training_acknowledgement_does_not_skip_interactive_confirmation(
    main_mod, headless_calls, monkeypatch, capsys
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    _set_startup_config(monkeypatch, ack=True)

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(
            _chat_args(model="muse-spark-1.2-contributor", provider="custom")
        )

    assert excinfo.value.code == 1
    assert not headless_calls
    assert "Model override cancelled" in capsys.readouterr().err


def test_cmd_chat_allows_interactive_gpt55_pro_when_confirmed(
    main_mod, headless_calls, codex_config, monkeypatch
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")

    main_mod.cmd_chat(_chat_args(model="openai/gpt-5.5-pro"))

    assert headless_calls["model"] == "openai/gpt-5.5-pro"


def test_cmd_chat_cancels_interactive_gpt55_pro_when_not_confirmed(
    main_mod, headless_calls, codex_config, monkeypatch, capsys
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(_chat_args(model="openai/gpt-5.5-pro"))

    assert excinfo.value.code == 1
    assert not headless_calls
    assert "Model override cancelled" in capsys.readouterr().err


def test_cmd_chat_cancels_interactive_gpt55_pro_on_eof(
    main_mod, headless_calls, codex_config, monkeypatch, capsys
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(_chat_args(model="openai/gpt-5.5-pro"))

    assert excinfo.value.code == 1
    assert not headless_calls
    assert "Model override cancelled" in capsys.readouterr().err


def test_cmd_chat_rejects_noninteractive_provider_only_override_when_default_is_expensive(
    main_mod, headless_calls, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"model": {"provider": "openai-codex", "default": "openai/gpt-5.5-pro"}},
    )

    with pytest.raises(SystemExit) as excinfo:
        main_mod.cmd_chat(_chat_args(model=None, provider="nous"))

    assert excinfo.value.code == 1
    assert not headless_calls
    assert "EXPENSIVE MODEL WARNING" in capsys.readouterr().err


def test_cmd_chat_allows_noninteractive_safe_codex_startup_override(
    main_mod, headless_calls, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"model": {"provider": "openai-codex", "default": "gpt-5.5"}},
    )

    main_mod.cmd_chat(_chat_args(model="gpt-5.5", provider="openai-codex"))

    assert headless_calls["model"] == "gpt-5.5"
    assert headless_calls["provider"] == "openai-codex"


def test_top_level_oneshot_rejects_noninteractive_gpt55_pro_startup_override(
    main_mod, codex_config, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", _NonInteractiveStdin())
    monkeypatch.setattr(sys, "argv", ["hermes", "-z", "hello", "-m", "openai/gpt-5.5-pro"])
    monkeypatch.setattr(main_mod, "_prepare_agent_startup", lambda _args: None)

    called = False

    def fake_run_and_exit_oneshot(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(main_mod, "_run_and_exit_oneshot", fake_run_and_exit_oneshot)

    with pytest.raises(SystemExit) as excinfo:
        main_mod.main()

    assert excinfo.value.code == 1
    assert called is False
    err = capsys.readouterr().err
    assert "EXPENSIVE MODEL WARNING" in err
    assert "non-interactive" in err


@pytest.mark.platforms("linux")
def test_public_finite_commands_refuse_expensive_routes_before_any_provider_request(tmp_path):
    """Disposable-pod integration: the unlocked CLI gate precedes the actual model boundary."""
    from tests.e2e.core.security import _helpers as harness
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text

    home = tmp_path / "home"
    key = harness.canary("sk-costguard")
    with FakeLLMServer([Text("cheap route accepted")], api_key=key) as provider:
        harness.write_home(home / ".hermes", provider.base_url, api_key=key)
        for args in (["chat", "-q", "do not call the provider"], ["-z", "do not call the provider"]):
            result = harness.run_hermes([*args, "--model", "openai/gpt-5.5-pro", "--provider", "custom"],
                                        home, timeout=90)
            assert result.returncode == 1, result.stdout + result.stderr
            assert "EXPENSIVE MODEL WARNING" in result.stderr
            assert provider.main_requests() == []
        accepted = harness.run_hermes(["chat", "-q", "use the cheap route", "--model", "safe-model",
                                       "--provider", "custom"], home, timeout=90)
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        assert "cheap route accepted" in accepted.stdout
        assert provider.main_requests()
