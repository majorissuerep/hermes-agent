"""Sandbox policy contracts: how config, presets and session overrides combine.

Pure — no kernel involvement; enforcement is proven live in test_sandbox_enforcement.py.
"""

from __future__ import annotations

import os

import pytest

from hermes_security.sandbox import launcher, seatbelt
from hermes_security.sandbox.policy import SandboxError, SessionOverrides, build_policy


def _modes(policy):
    return {g.path: g.mode for g in policy.expanded_grants()}


def test_grants_compose_config_preset_session_and_revocation(tmp_path):
    ws, extra, cfg_dir = tmp_path / "ws", tmp_path / "extra", tmp_path / "cfg"
    config = {"enabled": True, "default_presets": ["workspace"], "grants": [f"{cfg_dir}"],
              "tools": ["web"]}
    overrides = SessionOverrides(grants=[f"{extra}:rw"], tools=["file"], tools_removed=["web"],
                                 revoked=[str(cfg_dir)])
    policy = build_policy(config, overrides, workspace=str(ws))

    assert _modes(policy) == {str(ws): "rw", str(extra): "rw"}
    assert policy.tools == {"file"}
    # rw wins over ro for the same path, whatever order the grants arrive in.
    both = build_policy({"grants": [f"{ws}", f"{ws}:rw", f"{ws}"]}, SessionOverrides())
    assert _modes(both) == {str(ws): "rw"}


def test_readonly_subagents_lose_every_write_grant_but_keep_private_tmp(tmp_path):
    config = {"subagents": "readonly", "grants": [f"{tmp_path}/ws:rw"]}
    policy = build_policy(config, SessionOverrides(), tmp_dir=str(tmp_path / "tmp"))
    parent, child = policy.launch_spec(), policy.launch_spec(subagent=True)

    assert f"{tmp_path}/ws" in parent["write"] and f"{tmp_path}/ws" not in child["write"]
    assert f"{tmp_path}/ws" in child["read"]
    assert str(tmp_path / "tmp") in child["write"]


def test_unknown_preset_and_include_cycle_are_rejected():
    with pytest.raises(SandboxError, match="unknown sandbox preset"):
        build_policy({"default_presets": ["nope"]}, SessionOverrides())
    cyclic = {"a": {"include": ["b"]}, "b": {"include": ["a"]}}
    with pytest.raises(SandboxError, match="cycle"):
        build_policy({"default_presets": ["a"], "presets": cyclic}, SessionOverrides())


def test_nothing_granted_means_only_the_os_baseline_is_reachable(tmp_path):
    spec = build_policy({"enabled": True}, SessionOverrides()).launch_spec()
    home = os.path.expanduser("~")
    assert not any(p == home or p.startswith(home + os.sep) for p in spec["read"] + spec["write"])
    assert spec["network"] is False and spec["unix_sockets"] is False


def test_seccomp_program_encodes_the_network_and_socket_switches():
    closed = launcher.seccomp_program("x86_64", network=False, unix_sockets=False)
    net = launcher.seccomp_program("x86_64", network=True, unix_sockets=False)
    unix = launcher.seccomp_program("x86_64", network=False, unix_sockets=True)
    assert len({closed, net, unix}) == 3
    with pytest.raises(ValueError):
        launcher.seccomp_program("mips", network=False, unix_sockets=False)


def test_seatbelt_profile_denies_by_default_and_grants_resolved_paths(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    profile = seatbelt.build_profile({"read": [], "write": [str(link)], "network": False})
    assert "(deny default)" in profile
    assert f'(subpath "{os.path.realpath(real)}")' in profile
    assert "network-outbound (remote ip)" not in profile
    assert "network-outbound (remote ip)" in seatbelt.build_profile({"network": True})


def test_sandbox_run_keeps_the_top_level_command_name():
    """Regression: ``sandbox run``'s positional shadowed ``args.command`` and crashed the vault gate."""
    from hermes_cli.main import _build_cli_parser
    from hermes_cli.vault_gate import _is_read_only
    parser, _ = _build_cli_parser()
    args = parser.parse_args(["sandbox", "run", "--", "echo", "hi"])
    assert args.command == "sandbox" and args.run_command[-2:] == ["echo", "hi"]
    assert _is_read_only(args, args.command) is False
