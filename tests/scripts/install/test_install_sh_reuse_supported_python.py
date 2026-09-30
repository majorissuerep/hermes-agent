"""install.sh reuses an already-installed supported Python instead of downloading 3.11 (#10778)."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def _exe(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _run_prerequisites(tmp_path: Path, *, uv_find_script: str) -> subprocess.CompletedProcess[str]:
    """Run the real stage with healthy prerequisites and scripted managed uv."""
    home = tmp_path / "home"
    hermes_home = home / ".hermes"
    (hermes_home / "bin").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "python3.13", "#!/bin/sh\n[ \"$1\" = --version ] && echo 'Python 3.13.12'\nexit 0\n")
    _exe(hermes_home / "bin" / "uv", "#!/bin/sh\n[ \"$1\" = --version ] && { echo 'uv 0.9.0'; exit 0; }\n"
         "if [ \"$1\" = python ] && [ \"$2\" = find ]; then\n" + uv_find_script + "fi\n"
         "if [ \"$1\" = python ] && [ \"$2\" = install ]; then echo 'DOWNLOAD ATTEMPTED' >&2; exit 1; fi\nexit 0\n")
    versions = {
        "git": "git version 2.50.0",
        "node": "v26.7.0",
        "npm": "11.17.0",
        "curl": "curl 8.10.1",
        "rg": "ripgrep 14.1.1",
        "g++": "g++ 13.2.0",
        "c++": "c++ 13.2.0",
        "ffmpeg": "ffmpeg version 7.1.1",
    }
    for tool, version in versions.items():
        _exe(bin_dir / tool,
             f"#!/bin/sh\ncase \"$1\" in --version|-v|-version) echo {shlex.quote(version)};; esac\nexit 0\n")
    # This Python reuse probe must never depend on optional host binaries or
    # invoke the host's package manager when a prerequisite stub goes stale.
    unexpected = tmp_path / "unexpected-provisioning"
    for tool in ("sudo", "apt", "apt-get", "dnf", "pacman", "brew", "cargo", "pkg"):
        _exe(bin_dir / tool,
             '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> '
             + shlex.quote(str(unexpected)) + '\nexit 1\n')
    env = os.environ.copy()
    env.update({"HOME": str(home), "HERMES_HOME": str(hermes_home),
                "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', os.defpath)}"})
    bash = shutil.which("bash") or "/bin/bash"
    result = subprocess.run([bash, str(INSTALL_SH), "--stage", "prerequisites", "--non-interactive"],
                            env=env, stdin=subprocess.DEVNULL, encoding="utf-8", timeout=30,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    assert not unexpected.exists(), (
        unexpected.read_text(encoding="utf-8") + result.stdout
    )
    return result


@pytest.mark.linux_only
def test_supported_newer_python_is_reused_instead_of_downloading_311(tmp_path: Path) -> None:
    """3.11 absent, 3.13 present: the installer must take 3.13 and never call ``uv python install``."""
    result = _run_prerequisites(tmp_path, uv_find_script=(
        # The range probe must carry --system: with the install's own venv activated, a plain
        # `uv python find` returns venv/bin/python3, which setup_venv then deletes.
        f"  [ \"$3\" = 3.11 ] && exit 2\n  [ \"$3\" = --system ] && [ \"$4\" = '>=3.11,<3.14' ] && {{ echo {tmp_path}/bin/python3.13; exit 0; }}\n  exit 2\n"))
    assert result.returncode == 0, result.stdout
    assert "DOWNLOAD ATTEMPTED" not in result.stdout, result.stdout
    assert "Python found: Python 3.13.12" in result.stdout, result.stdout
