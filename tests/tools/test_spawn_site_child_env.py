"""Real children started by the production spawn sites observe the secret scrub."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.tools._child_env_fixtures import child_env

_TIER1 = ("TELEGRAM_BOT_TOKEN", "GATEWAY_RELAY_SECRET")
_PROVIDER = "OPENAI_API_KEY"


def _plant(monkeypatch):
    for name in (*_TIER1, _PROVIDER):
        monkeypatch.setenv(name, f"fake-{name.lower()}")


def _probe_script(path: Path, out: Path, names) -> Path:
    path.write_text(
        f"#!{sys.executable}\nimport json, os\n"
        f"open({str(out)!r}, 'w').write(json.dumps({{n: os.environ.get(n) for n in {list(names)!r}}}))\n",
        encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.platforms("posix")  # the stand-in binaries are shebang scripts
@pytest.mark.parametrize("site", ["lsp_server", "lsp_go_install", "lsp_npm_install"])
def test_third_party_children_never_see_hermes_credentials(child_env, monkeypatch, site):
    _plant(monkeypatch)
    # Profile home mode (the container default) re-points HOME; the CLIs whose own logins live
    # under the user's HOME get it back, language servers and installers follow the terminal.
    monkeypatch.setenv("TERMINAL_HOME_MODE", "profile")
    (child_env / "hermes" / "home").mkdir(parents=True, exist_ok=True)
    out = child_env / "seen.json"
    own = {"lsp_server": "LSP_OWN_SETTING", "lsp_go_install": "GOBIN", "lsp_npm_install": "PATH",
           "raft_bridge": "RAFT_CHANNEL_TOKEN", "buzz_cli": "BUZZ_PRIVATE_KEY"}[site]
    probe = _probe_script(child_env / "probe", out, [*_TIER1, _PROVIDER, own, "HOME"])

    if site == "lsp_server":
        from agent.lsp.client import LSPClient

        async def _run():
            client = LSPClient(server_id="probe", workspace_root=str(child_env), command=[str(probe)],
                               env={"LSP_OWN_SETTING": "kept"})
            await client._spawn()
            await client._proc.wait()
            for task in (client._reader_task, client._stderr_task):
                task.cancel()
        asyncio.run(_run())
    elif site == "lsp_go_install":
        from agent.lsp import install

        with patch.object(install.shutil, "which", return_value=str(probe)):
            install._install_go("example.com/probe@latest", "probe")
    elif site == "lsp_npm_install":
        from agent.lsp import install

        with patch.object(install, "find_node_executable", return_value=str(probe)):
            install._install_npm("probe-language-server", "probe")
    seen = json.loads(out.read_text(encoding="utf-8-sig"))
    assert {k: seen[k] for k in (*_TIER1, _PROVIDER)} == dict.fromkeys((*_TIER1, _PROVIDER))
    assert seen[own]  # the child's own configuration still arrives
    user_home = False
    assert seen["HOME"] == str(child_env if user_home else child_env / "hermes" / "home")
