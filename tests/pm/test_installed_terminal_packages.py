"""PM's installed package map works independently of a checkout working directory."""

import json
from pathlib import Path
import subprocess
import sys


def test_native_packages_resolve_installed_candidate_from_neutral_cwd(tmp_path):
    packages = ["agent", "tools", "hermes_cli", "hermes_state", "hermes_security",
                "hermes_platform", "cron", "pm", "providers", "tui_runtime"]
    result = subprocess.run([
        sys.executable, "-I", "-c",
        "import importlib.util,json,sys; "
        "print(json.dumps({name:(spec.origin if (spec:=importlib.util.find_spec(name)) else None) "
        "for name in sys.argv[1:]}))", *packages,
    ], cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    origins = json.loads(result.stdout)
    candidate = Path(__file__).resolve().parents[2]
    for package in packages:
        assert origins[package] is not None, f"{package} is absent from the installed package map"
        assert Path(origins[package]).resolve().is_relative_to(candidate), (package, origins[package])
