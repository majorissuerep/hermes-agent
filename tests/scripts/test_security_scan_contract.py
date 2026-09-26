"""Exercise the real scanner gate with explicit engine outcomes, not text snapshots."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ("shellcheck", "gitleaks", "bandit", "pip-audit", "semgrep", "hadolint", "npm", "trivy")
STUB = r'''
import os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ['SCANNER_CALLS'], 'a') as calls:
    calls.write(name + '\n')
fault = os.environ.get('FAULT_TOOL') == name
mode = os.environ.get('FAULT_MODE') if fault else 'clean'
if mode == 'error':
    print('synthetic engine failure', file=sys.stderr)
    sys.exit(2)
if mode == 'finding':
    print('warning: findings; Issue Severity; 1 vulnerability')
    if name == 'semgrep' and '--error' not in sys.argv:
        sys.exit(0)
    if name == 'trivy' and '--exit-code' not in sys.argv:
        sys.exit(0)
    sys.exit(1)
if name == 'pip-audit':
    print('No known vulnerabilities found')
if name == 'npm':
    print('found 0 vulnerabilities')
'''


@pytest.fixture
def scanner(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts" / "security_scan.sh", root / "scripts" / "security_scan.sh")
    for workspace in ("ui-tui", "web", "apps/desktop", "apps/shared", "apps/bootstrap-installer"):
        folder = root / workspace
        folder.mkdir(parents=True)
        (folder / "package.json").write_text("{}")
    (root / "package-lock.json").write_text("{}")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("dirname", "find", "tee", "grep", "head", "tail", "mktemp", "rm", "cat"):
        binary = shutil.which(name)
        assert binary, f"fixture needs {name}"
        (binaries / name).symlink_to(binary)
    for name in TOOLS:
        tool = binaries / name
        tool.write_text(f"#!{sys.executable}\n" + STUB)
        tool.chmod(0o700)
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    site = root / ".venv/site-packages"
    site.mkdir()
    python.write_text(f"#!{sys.executable}\nprint({str(site)!r})\n")
    python.chmod(0o700)
    calls = tmp_path / "calls"
    env = {"PATH": str(binaries), "HOME": str(tmp_path), "SCANNER_CALLS": str(calls)}
    bash = shutil.which("bash")
    assert bash

    def run(tool="", mode="clean"):
        if mode == "missing":
            (binaries / tool).unlink()
        return subprocess.run([bash, str(root / "scripts" / "security_scan.sh")],
                              env=dict(env, FAULT_TOOL=tool, FAULT_MODE=mode),
                              capture_output=True, text=True, timeout=30)
    return run, calls


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("mode", ["missing", "finding", "error"])
def test_missing_findings_and_errors_never_pass(scanner, tool, mode):
    run, _ = scanner
    result = run(tool, mode)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "ALL CLEAN" not in result.stdout


@pytest.mark.parametrize("missing", [".venv/bin/python", ".venv/site-packages"])
def test_security_gate_requires_project_environment(scanner, tmp_path, missing):
    target = tmp_path / "repo" / missing
    if target.is_dir():
        target.rmdir()
    else:
        target.unlink()
    run, _ = scanner
    result = run()
    assert result.returncode != 0, result.stdout


def test_all_clean_means_every_required_engine_executed(scanner):
    run, calls = scanner
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL CLEAN" in result.stdout
    assert set(calls.read_text().splitlines()) == set(TOOLS)
