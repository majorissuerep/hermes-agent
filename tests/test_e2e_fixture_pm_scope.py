"""Threaded provider setup cannot change another PM preparation's owning home."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

from tests.e2e.vault_home import _fixture_pm_home


def test_parallel_fixture_pm_preparation_keeps_one_process_home(tmp_path):
    import hermes_constants as constants
    from pm.environments import installs_root

    homes = [tmp_path / f"owner-{index}" for index in range(8)]
    for home in homes:
        home.mkdir()
    start = Barrier(len(homes))
    original = os.environ.get("HERMES_HOME")
    original_pin = constants._PINNED_PROCESS_HERMES_HOME

    def prepare(home):
        observations = []
        for iteration in range(4):
            start.wait(timeout=10)  # outside the scope: PM preparation may serialize
            with _fixture_pm_home(home):
                for index in range(12):
                    # Filesystem calls release the GIL like PM's acquisition and
                    # facts reads; there is no timing-based absence assertion.
                    state = installs_root()
                    state.mkdir(parents=True, exist_ok=True)
                    receipt = state / f"{home.name}-{iteration}-{index}"
                    receipt.write_text(home.name)
                    assert receipt.read_text() == home.name
                    observations.append((os.environ["HERMES_HOME"], constants._PINNED_PROCESS_HERMES_HOME,
                                         constants.get_hermes_home(), state))
        return home, observations

    with ThreadPoolExecutor(max_workers=len(homes)) as pool:
        results = list(pool.map(prepare, homes))
    for home, observations in results:
        assert observations
        assert all(row == (str(home), str(home), home, home / "installs") for row in observations), (
            home, observations)
    assert os.environ.get("HERMES_HOME") == original
    assert constants._PINNED_PROCESS_HERMES_HOME == original_pin
