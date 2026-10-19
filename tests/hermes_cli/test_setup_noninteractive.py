"""Tests for non-interactive setup and first-run headless behavior."""

from argparse import Namespace
from unittest.mock import patch

from hermes_cli.config import DEFAULT_CONFIG, load_config, save_config


def _make_setup_args(**overrides):
    return Namespace(
        non_interactive=overrides.get("non_interactive", False),
        section=overrides.get("section", None),
        reset=overrides.get("reset", False),
    )


def _make_chat_args(**overrides):
    return Namespace(
        continue_last=overrides.get("continue_last", None),
        resume=overrides.get("resume", None),
        model=overrides.get("model", None),
        provider=overrides.get("provider", None),
        toolsets=overrides.get("toolsets", None),
        verbose=overrides.get("verbose", False),
        query=overrides.get("query", None),
        worktree=overrides.get("worktree", False),
        yolo=overrides.get("yolo", False),
        pass_session_id=overrides.get("pass_session_id", False),
        quiet=overrides.get("quiet", False),
        checkpoints=overrides.get("checkpoints", False),
    )


class TestNonInteractiveSetup:
    """Verify setup paths exit cleanly in headless/non-interactive environments."""





    def test_reset_flag_rewrites_config_before_noninteractive_exit(self, tmp_path, monkeypatch, capsys):
        """--reset should rewrite config.yaml even when the wizard cannot run interactively."""
        from hermes_cli.setup import run_setup_wizard

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        cfg = load_config()
        cfg["model"] = {"provider": "custom", "base_url": "http://localhost:8080/v1", "default": "llama3"}
        cfg["agent"]["max_turns"] = 12
        save_config(cfg)

        args = _make_setup_args(non_interactive=True, reset=True)

        run_setup_wizard(args)

        reloaded = load_config()
        assert reloaded["model"] == DEFAULT_CONFIG["model"]
        assert reloaded["agent"]["max_turns"] == DEFAULT_CONFIG["agent"]["max_turns"]
        out = capsys.readouterr().out
        assert "Configuration reset to defaults." in out

    def test_chat_without_query_leaves_first_run_to_native_tui(self):
        """Non-TTY input must not revive the removed classic setup/REPL route."""
        from hermes_cli.main import cmd_chat

        args = _make_chat_args()

        with (
            patch("hermes_cli.main._has_any_provider_configured", return_value=False),
            patch("hermes_cli.main.cmd_setup") as mock_setup,
            patch("hermes_cli.main._launch_tui", return_value=17) as launch,
            patch("sys.stdin") as mock_stdin,
            patch("builtins.input", side_effect=AssertionError("input should not be called")),
        ):
            mock_stdin.isatty.return_value = False
            result = cmd_chat(args)

        assert result == 17
        launch.assert_called_once()
        mock_setup.assert_not_called()

