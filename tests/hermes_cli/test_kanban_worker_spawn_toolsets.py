from __future__ import annotations

import pytest
import subprocess


def _make_task(kb, *, assignee: str):
    return kb.Task(
        id="t_spawn_tools",
        title="spawn tools",
        body=None,
        assignee=assignee,
        status="running",
        priority=0,
        created_by="test",
        created_at=1,
        started_at=None,
        completed_at=None,
        workspace_kind="dir",
        workspace_path=None,
        claim_lock="lock",
        claim_expires=None,
        tenant=None,
        current_run_id=7,
    )


def test_default_spawn_pins_assignee_profile_cli_toolsets(monkeypatch, tmp_path):
    """The transport client reaches the assigned profile with its exact run claim."""
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "elias"
    profile.mkdir(parents=True)
    profile.joinpath("config.yaml").write_text(
        """
platform_toolsets:
  cli:
    - clarify
    - code_execution
    - delegation
    - file
    - memory
    - session_search
    - skills
    - terminal
    - web
toolsets:
  - hermes-cli
agent:
  disabled_toolsets: []
""".lstrip(),
        encoding="utf-8",
    )
    root.joinpath("config.yaml").write_text("toolsets:\n  - kanban\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(root))

    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_dispatch as kbd

    monkeypatch.setattr(kbd, "_resolve_hermes_argv", lambda: ["hermes"])

    captured = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(kwargs.get("env") or {})
        captured["cwd"] = kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pid = kbd._default_spawn(_make_task(kb, assignee="elias"), str(workspace))

    assert pid == 4242
    assert captured["env"]["HERMES_HOME"] == str(profile)
    assert captured["env"]["HERMES_KANBAN_TASK"] == "t_spawn_tools"
    assert captured["cmd"][-2:] == ["-m", "hermes_cli.kanban_worker_client"]
    assert captured["env"]["HERMES_KANBAN_RUN_ID"] == "7"
    assert captured["env"]["HERMES_KANBAN_CLAIM_LOCK"] == "lock"


def test_default_spawn_does_not_pass_mutable_launch_overrides(monkeypatch, tmp_path):
    """Owner policy reads overrides from the claimed card, not executable argv."""
    root = tmp_path / ".hermes"
    (root / "profiles" / "elias").mkdir(parents=True)
    root.joinpath("config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(root))

    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_dispatch as kbd

    monkeypatch.setattr(kbd, "_resolve_hermes_argv", lambda: ["hermes"])
    captured = {}

    class FakeProc:
        pid = 4244

    def fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = _make_task(kb, assignee="elias")
    task.model_override = "gpt-5.6-sol"
    kbd._default_spawn(task, str(workspace))

    # Mutable model strings are not launch authority; the owner reads the claimed card.
    assert captured["cmd"][-2:] == ["-m", "hermes_cli.kanban_worker_client"]
    assert task.model_override not in captured["cmd"]

def test_default_spawn_resolves_env_passthrough_under_multiplex(monkeypatch, tmp_path):
    """Under multiplex a worker spawn with ``terminal.env_passthrough`` configured must
    forward the ASSIGNEE profile's own value, never crash on an unscoped read or leak the
    dispatcher's ambient os.environ (#109494).
    """
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "elias"
    profile.mkdir(parents=True)
    root.joinpath("config.yaml").write_text(
        "terminal:\n  env_passthrough:\n    - MY_PASSTHROUGH_VAR\n", encoding="utf-8")
    profile.joinpath("config.yaml").write_text("{}\n", encoding="utf-8")
    profile.joinpath(".env").write_text("MY_PASSTHROUGH_VAR=elias-value\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("MY_PASSTHROUGH_VAR", "dispatcher-value")

    from agent.secret_scope import set_multiplex_active
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_dispatch as kbd

    monkeypatch.setattr(kbd, "_resolve_hermes_argv", lambda: ["hermes"])

    captured = {}

    class FakeProc:
        pid = 4243

    def fake_popen(cmd, *args, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    set_multiplex_active(True)
    try:
        pid = kbd._default_spawn(_make_task(kb, assignee="elias"), str(workspace))
    finally:
        set_multiplex_active(False)

    assert pid == 4243
    # The assignee's own scoped value, not the dispatcher's ambient os.environ one.
    assert captured["env"].get("MY_PASSTHROUGH_VAR") == "elias-value"


def test_worker_toolsets_come_from_the_assignee_profile_not_the_parent_config(tmp_path, monkeypatch):
    """A valid native worker reads its assignee's CLI policy, not the parent's config."""
    from contextlib import closing

    import json

    from hermes_cli import kanban_db as kb
    from hermes_cli import oneshot
    from hermes_cli.kanban_db_connect import connect
    from hermes_cli.kanban_worker_client import _claim_context

    profile = tmp_path / "profiles" / "elias"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.setattr("hermes_cli.profiles.resolve_profile_env", lambda name: str(profile))
    root_config = {"platform_toolsets": {"cli": ["kanban"]}}
    (tmp_path / "config.yaml").write_text(json.dumps(root_config), encoding="utf-8")
    assignee_config = {"model": {"default": "worker-model", "provider": "custom"},
                       "platform_toolsets": {"cli": ["terminal", "web"]}}
    (profile / "config.yaml").write_text(json.dumps(assignee_config), encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    kb.create_board("owned")
    with closing(connect(board="owned")) as conn:
        task_id = kb.create_task(conn, title="t", body="b", assignee="elias",
                                 workspace_kind="dir", workspace_path=str(workspace))
        kb.recompute_ready(conn)
        task = kb.claim_task(conn, task_id)
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setenv("HERMES_KANBAN_DB", db_path)
    monkeypatch.setenv("HERMES_KANBAN_TASK", task.id)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(task.current_run_id))
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", task.claim_lock)
    claimed, context = _claim_context()
    assert claimed.assignee == "elias"
    assert context["task_id"] == task.id and context["run_id"] == task.current_run_id

    constructed = {}

    class FakeAgent:
        session_id = None

        def __init__(self, **kwargs):
            constructed.update(kwargs)

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(oneshot, "_create_session_db_for_oneshot", lambda: None)
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_with_fallback",
                        lambda *args, **kwargs: ({"provider": "custom", "api_key": "fixture"}, None))
    monkeypatch.setattr("hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build", lambda **kwargs: None)
    monkeypatch.setattr("run_agent.AIAgent", FakeAgent)
    output, _ = oneshot._run_agent("work", conversation_runner=lambda *_: {"final_response": "done"})
    assert output == "done"
    assert {"terminal", "web"} <= set(constructed["enabled_toolsets"])
    assert constructed["model"] == "worker-model"

    # Another owning profile cannot consume the same valid card/claim.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="kanban_profile_mismatch"):
        _claim_context()
