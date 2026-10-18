"""Slow suites are repository trees, not directory names reserved everywhere."""
from scripts import run_tests_parallel as runner


def _tree(root, paths):
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_default_discovery_includes_mirrored_fast_tests_and_ignores_checkout_ancestors(tmp_path, monkeypatch):
    root = tmp_path / "e2e" / "checkout"
    included = {"tests/test_plain.py", "tests/scripts/e2e/test_driver.py",
                "tests/tools/docker/test_adapter.py", "tests/agent/integration/test_contract.py"}
    excluded = {f"tests/{name}/test_live.py" for name in ("integration", "e2e", "docker")}
    _tree(root, included | excluded)
    monkeypatch.setattr(runner, "__file__", str(root / "scripts" / "run_tests_parallel.py"))

    assert {path.relative_to(root).as_posix() for path in runner._discover_files([root / "tests"])} == included


def test_explicit_slow_subtree_and_file_overrides_do_not_include_other_slow_trees(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    _tree(root, {"tests/docker/nested/test_live.py", "tests/e2e/test_live.py",
                 "tests/integration/test_live.py", "tests/scripts/e2e/test_driver.py"})
    monkeypatch.setattr(runner, "__file__", str(root / "scripts" / "run_tests_parallel.py"))
    roots = [root / "tests", root / "tests/docker", root / "tests/e2e/test_live.py"]

    assert {path.relative_to(root).as_posix() for path in runner._discover_files(roots)} == {
        "tests/docker/nested/test_live.py", "tests/e2e/test_live.py", "tests/scripts/e2e/test_driver.py"}

    # --include-integration clears the exclusions before discovery.
    monkeypatch.setattr(runner, "_SKIP_PARTS", set())
    assert len(runner._discover_files([root / "tests"])) == 4
