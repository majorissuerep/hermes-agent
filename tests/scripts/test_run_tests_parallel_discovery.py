"""Slow suites are repository trees, not directory names reserved everywhere."""
import argparse
import os
import subprocess
import sys
import pytest

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


@pytest.mark.parametrize("mode", ["positional", "paths", "files", "files_from"])
def test_missing_explicit_target_rejects_the_entire_selection_before_ignores(tmp_path, capsys, mode):
    valid = tmp_path / "test_valid.py"
    valid.write_text("def test_valid(): pass\n")
    missing = tmp_path / "test_missing.py"
    args = argparse.Namespace(files=None, files_from=None, paths_positional=[], paths="tests",
                              include_integration=False)
    if mode == "positional":
        args.paths_positional = [str(valid), str(missing)]
    elif mode == "paths":
        args.paths = os.pathsep.join(map(str, (valid, missing)))
    elif mode == "files":
        args.files = os.pathsep.join(map(str, (valid, missing)))
    else:
        manifest = tmp_path / "files.txt"
        manifest.write_text(f"{valid}\n{missing}\n")
        args.files_from = str(manifest)

    with pytest.raises(SystemExit) as error:
        runner._select_files(args, [f"--ignore={missing}"], tmp_path)
    assert error.value.code == 2
    assert str(missing) in capsys.readouterr().err


def test_public_runner_rejects_missing_target_without_executing_valid_sibling(tmp_path):
    marker = tmp_path / "executed"
    valid = tmp_path / "test_valid.py"
    valid.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef test_valid(): pass\n")
    missing = tmp_path / "test_missing.py"
    result = subprocess.run([sys.executable, runner.__file__, "--file-retries", "0", str(valid), str(missing)],
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 2 and str(missing) in result.stderr
    assert not marker.exists()
