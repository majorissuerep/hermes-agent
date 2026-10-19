"""Tests for /personality none — clearing personality overlay.

Updated for the single-owner unification (hermes_cli.personality): built-ins
always exist, resolution reads config (agent.personalities overlays), and
persistence flows exclusively through persist_personality().
"""
from unittest.mock import MagicMock, patch

# ── CLI tests ──────────────────────────────────────────────────────────────

class TestCLIPersonalityNone:

    def _make_cli(self, personalities=None):
        from cli import HermesCLI
        from hermes_cli.personality import available_personalities

        cli = HermesCLI.__new__(HermesCLI)
        user = personalities or {
            "helpful": "You are helpful.",
            "concise": "You are concise.",
        }
        cli.config = {"agent": {"personalities": user}}
        cli.personalities = available_personalities(cli.config)
        cli.system_prompt = "You are kawaii~"
        cli.agent = MagicMock()
        cli.console = MagicMock()
        return cli

    def test_set_persists_display_personality_not_system_prompt(self):
        cli = self._make_cli()
        saves = []

        def _persist(name):
            saves.append(("display.personality", name))
            return True

        with patch("hermes_cli.personality.persist_personality", side_effect=_persist):
            cli._handle_personality_command("/personality helpful")

        assert cli.system_prompt == "You are helpful."
        assert ("display.personality", "helpful") in saves
        assert not any(k == "agent.system_prompt" for k, _ in saves)

    def test_neutral_restores_manual_system_prompt_without_wiping_config(self):
        cli = self._make_cli()
        saves = []

        def _persist(name):
            saves.append(("display.personality", name))
            return True

        with (
            patch("hermes_cli.personality.persist_personality", side_effect=_persist),
            patch(
                "hermes_cli.config.read_raw_config",
                return_value={"agent": {"system_prompt": "manual forever"}},
            ),
        ):
            cli._handle_personality_command("/personality neutral")

        assert cli.system_prompt == "manual forever"
        assert ("display.personality", "") in saves
        assert not any(k == "agent.system_prompt" for k, _ in saves)

    def test_builtin_personality_works_without_config_entry(self):
        # Built-ins come from hermes_cli.personality, not from config.
        cli = self._make_cli(personalities={})
        with patch("hermes_cli.personality.persist_personality", return_value=True):
            cli._handle_personality_command("/personality kawaii")
        assert "kawaii" in cli.system_prompt.lower()

# ── Gateway tests ──────────────────────────────────────────────────────────


class TestPersonalityDictFormat:
    """Test dict-format custom personalities with description, tone, style."""

    def _make_cli(self, personalities):
        from cli import HermesCLI
        from hermes_cli.personality import available_personalities

        cli = HermesCLI.__new__(HermesCLI)
        cli.config = {"agent": {"personalities": personalities}}
        cli.personalities = available_personalities(cli.config)
        cli.system_prompt = ""
        cli.agent = None
        cli.console = MagicMock()
        return cli

    def test_dict_personality_uses_system_prompt(self):
        cli = self._make_cli({
            "coder": {
                "description": "Expert programmer",
                "system_prompt": "You are an expert programmer.",
                "tone": "technical",
                "style": "concise",
            }
        })
        with patch("hermes_cli.personality.persist_personality", return_value=True):
            cli._handle_personality_command("/personality coder")
        assert "You are an expert programmer." in cli.system_prompt
