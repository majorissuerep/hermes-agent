"""One native runtime never lends a sibling profile's secrets to an outgoing MCP peer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import hermes_yaml as yaml

from tests.e2e.core.security import _helpers as H
from tests.e2e.core.security import _scope as S
from tests.e2e.core.security._stdio import NativeRuntime
from tests.fakes.fake_llm_provider import FakeLLMServer, Text

pytestmark = pytest.mark.platforms("posix")


def test_mcp_subprocess_env_holds_only_own_profile_secrets(tmp_path: Path) -> None:
    home = tmp_path / "home"
    default = home / ".hermes"
    profiles = {"default": default, "beta": default / "profiles" / "beta"}
    keys = {name: H.canary(f"sk-provider-{name}") for name in profiles}
    secrets = {name: H.canary(f"scope-{name}") for name in profiles}
    server = S.write_env_dump_server(tmp_path / "envdump_server.py")
    dumps = {name: tmp_path / f"mcp-env-{name}.json" for name in profiles}

    with (FakeLLMServer([Text("default ready")], api_key=keys["default"]) as first,
          FakeLLMServer([Text("beta ready")], api_key=keys["beta"]) as second):
        for (name, path), provider in zip(profiles.items(), (first, second)):
            extra = yaml.safe_dump({
                "mcp_servers": S.mcp_config(server, dumps[name]),
                "mcp_discovery_timeout": 90,
                "onboarding": {"profile_build": "off"},
            })
            H.write_home(path, provider.base_url, api_key=keys[name], config=extra,
                         env={S.SECRET_ENV: secrets[name]})
            assert (path / ".env").read_bytes().startswith(b"HRMVAULT\0")

        runtime = NativeRuntime(home, tmp_path / "native-runtime.log")
        try:
            for name in profiles:
                params = {} if name == "default" else {"profile": name}
                sid = runtime.ok("session.create", params)["session_id"]
                runtime.turn(sid, "Initialize the configured local MCP tools.")

            envs = {name: H.poll(lambda p=path: p.is_file() and json.loads(p.read_text()),
                                90, f"{name} MCP environment") for name, path in dumps.items()}
            for name, env in envs.items():
                assert env.get(S.MCP_ENV_KEY) == secrets[name], f"{name}: own secret was not resolved"
                blob = json.dumps(env)
                foreign = [value for owner, value in secrets.items() if owner != name]
                assert not any(value in blob for value in foreign), f"{name}: sibling secret leaked"
                assert not any(value in blob for value in keys.values()), f"{name}: provider key leaked"
                assert "OPENAI_API_KEY" not in env, f"{name}: ambient provider key was passed"
        finally:
            runtime.close()
        assert first.main_requests() and second.main_requests(), "both profile routes must execute"
