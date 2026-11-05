"""Interactive and headless owners resolve the same documented turn-limit policy."""

import sys

import pytest

from hermes_cli.agent_limits import configured_turn_limit


@pytest.mark.parametrize(("config", "override", "expected"), [
    ({"agent": {"max_turns": 2}}, None, 2),
    ({"agent": {"max_turns": 2}}, 1, 1),
    ({"agent": {"max_turns": 2}}, 0, sys.maxsize),
    ({"agent": {"max_turns": "unlimited"}}, None, sys.maxsize),
    ({"max_turns": 7}, None, 7),
    ({}, None, 4),
])
def test_turn_limit_precedence_and_explicit_unlimited(monkeypatch, config, override, expected):
    monkeypatch.setenv("HERMES_MAX_ITERATIONS", "4")
    assert configured_turn_limit(config, override) == expected
