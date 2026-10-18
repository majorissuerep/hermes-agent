"""Ink is the interactive default; finite native turns never become a UI launch."""
from argparse import Namespace
import pytest
from hermes_cli import main


@pytest.mark.parametrize("argv,expected", [([], True), (["chat"], True),
    (["--profile", "coder", "chat"], True), (["config", "get", "model"], False),
    (["-q", "task"], False), (["chat", "--query=task"], False),
    (["--query-file", "task.txt"], False), (["-z", "task"], False),
    (["chat", "--safe-mode", "-Q", "-q", "task"], False)])
def test_early_selection_never_reads_profile_configuration(monkeypatch, argv, expected):
    from hermes_cli import config
    def forbidden(*args, **kwargs):
        pytest.fail("entry selection must not read profile configuration")
    monkeypatch.setattr(config, "load_config", forbidden)
    monkeypatch.setattr(config, "read_user_config_raw", forbidden)
    assert main._wants_tui_early(argv) is expected


@pytest.mark.parametrize("override", [{}, {"query": "task"}, {"query_file": "task.txt"},
    {"oneshot": "task"}, {"oneshot_exit": True}])
def test_native_finite_selection_ignores_retired_preferences(monkeypatch, override):
    monkeypatch.setenv("HERMES_TUI", "1")
    args = Namespace(command="chat", **override)
    assert main._resolve_use_tui(args) is (not bool(override))
    assert main._is_tui_chat_launch(args) is (not bool(override))


def test_parser_retains_native_terminal_options_but_rejects_classic():
    from hermes_cli._parser import build_top_level_parser
    parser, _, _ = build_top_level_parser()
    for argv in (["--native"], ["chat", "--tui-native"]):
        assert parser.parse_args(argv).tui_native
    assert parser.parse_args(["chat", "--tui"]).tui
    for argv in (["--cli"], ["chat", "--classic"]):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(argv)
        assert error.value.code == 2
