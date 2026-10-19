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
