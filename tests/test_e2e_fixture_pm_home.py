"""Fixture preparation must provision the same home as its measured child."""

import os

import pytest


@pytest.mark.parametrize("fail", [False, True])
def test_fixture_pm_home_binds_and_restores_all_home_readers(tmp_path, monkeypatch, fail):
    import hermes_constants as constants
    from tests.e2e.vault_home import _fixture_pm_home

    parent, target, pinned = (tmp_path / name for name in ("parent", "target", "pinned"))
    monkeypatch.setenv("HERMES_HOME", str(parent))
    monkeypatch.setattr(constants, "_PINNED_PROCESS_HERMES_HOME", str(pinned))
    previous = constants.get_hermes_home()

    def prepare():
        with _fixture_pm_home(target):
            assert os.environ["HERMES_HOME"] == str(target)
            assert constants.get_hermes_home() == target
            assert constants.get_default_hermes_root() == target
            assert constants.get_routing_process_hermes_home() == target
            if fail:
                raise RuntimeError("preparation failed")

    if fail:
        with pytest.raises(RuntimeError, match="preparation failed"):
            prepare()
    else:
        prepare()
    assert os.environ["HERMES_HOME"] == str(parent)
    assert constants.get_hermes_home() == previous
    assert constants.get_default_hermes_root() == parent
    assert constants.get_routing_process_hermes_home() == pinned
