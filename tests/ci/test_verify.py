"""The shared verification plan fails open when the changed consumers are unknown."""
import pytest

from scripts.ci.verify import plan


@pytest.mark.parametrize("paths", [[".woodpecker/verify.yml"], ["scripts/ci/verify.py"], [], [".github/workflows/ci.yaml"]])
def test_unknown_or_ci_changes_select_every_consumer(paths):
    assert all(plan(paths, "pull_request").values())


def test_known_frontend_change_selects_its_consumers_and_main_checks_everything():
    paths = ["ui-tui/src/app.tsx"]
    assert plan(paths, "pull_request") == {"python": False, "frontend": True, "site": False}
    assert all(plan(paths, "push").values())
