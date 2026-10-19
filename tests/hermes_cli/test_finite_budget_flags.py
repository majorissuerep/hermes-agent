"""Both public finite entry forms honor explicit turn/time ceilings."""
import pytest

from hermes_cli._parser import build_top_level_parser


@pytest.mark.parametrize("argv", [
    ["-z", "bounded task", "--max-turns", "10", "--run-budget", "60"],
    ["chat", "-q", "bounded task", "--max-turns", "10", "--run-budget", "60"],
    ["--max-turns", "10", "--run-budget", "60", "chat", "-q", "bounded task"],
])
def test_finite_launch_budgets_survive_parser_boundaries(argv):
    parser, _, _ = build_top_level_parser()
    args = parser.parse_args(argv)
    assert args.max_turns == 10
    assert args.run_budget == 60


@pytest.mark.parametrize("argv", [
    ["-z", "bounded task", "--base-url", "http://127.0.0.1:9999/v1", "--api-key", "fixture-only"],
    ["chat", "-q", "bounded task", "--base-url", "http://127.0.0.1:9999/v1", "--api-key", "fixture-only"],
    ["--base-url", "http://127.0.0.1:9999/v1", "--api-key", "fixture-only", "chat", "-q", "bounded task"],
])
def test_finite_launch_endpoint_is_not_misclassified_as_a_command(argv):
    from hermes_cli._parser import command_argv

    parser, _, _ = build_top_level_parser()
    args = parser.parse_args(argv)
    assert args.base_url == "http://127.0.0.1:9999/v1"
    assert args.api_key == "fixture-only"
    assert command_argv(argv)[:1] == ([] if argv[0] == "-z" else ["chat"])


def test_combined_oneshot_value_options_leave_no_spurious_subcommand(tmp_path):
    from hermes_cli._parser import command_argv

    usage = str(tmp_path / "usage report.json")
    argv = ["-z", "bounded task", "--usage-file", usage, "--toolsets", "delegation",
            "--reasoning", "high", "--provider", "custom", "--model", "test-model",
            "--base-url", "http://127.0.0.1:9999/v1", "--api-key", "fixture-only",
            "--max-turns", "10", "--run-budget", "60"]
    parser, _, _ = build_top_level_parser()
    args = parser.parse_args(argv)
    assert command_argv(argv) == []
    assert args.usage_file == usage and args.toolsets == "delegation" and args.reasoning == "high"
    assert (args.provider, args.model, args.max_turns, args.run_budget) == ("custom", "test-model", 10, 60)


def test_query_file_remains_a_native_chat_selector(tmp_path):
    from hermes_cli._parser import command_argv

    query = str(tmp_path / "query file.txt")
    argv = ["chat", "--query-file", query, "--provider", "custom", "--base-url", "http://127.0.0.1:9999/v1"]
    parser, _, _ = build_top_level_parser()
    args = parser.parse_args(argv)
    assert args.query_file == query and args.base_url == "http://127.0.0.1:9999/v1"
    assert command_argv(argv)[0] == "chat"
