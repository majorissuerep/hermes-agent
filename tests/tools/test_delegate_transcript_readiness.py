"""Native queued children publish an encrypted transcript before their first turn."""
import json

import pytest

from tests.e2e.vault_home import seal_fixture_home


@pytest.fixture
def parent(tmp_path, monkeypatch):
    from run_agent import AIAgent
    from hermes_state import SessionDB

    home = tmp_path / "profile"
    home.mkdir()
    seal_fixture_home(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("agent.model_metadata.get_model_context_length", lambda *a, **kw: 128000)
    monkeypatch.setattr("agent.context_compressor.get_model_context_length", lambda *a, **kw: 128000)
    database = SessionDB(home / "state.db")
    agent = AIAgent(session_db=database, session_id="parent", model="synthetic-model", provider="custom",
                    api_key="synthetic-unused-key", base_url="http://127.0.0.1:9/v1", api_mode="chat_completions",
                    platform="cli", quiet_mode=True, skip_context_files=True, skip_memory=True,
                    save_trajectories=False, enabled_toolsets=["file"])
    agent._ensure_db_session()
    try:
        yield agent, home
    finally:
        agent.close()
        database.close()


def _build(parent):
    from tools.delegate_tool import _build_child_agent

    return _build_child_agent(task_index=0, goal="Inspect synthetic data", context=None,
                              toolsets=["file"], model=None, max_iterations=2, task_count=1,
                              parent_agent=parent)


def test_published_child_is_readable_and_first_turn_completes_initialization(parent):
    from agent.turn_facade_lease import admit_durable_turn_lease

    agent, home = parent
    published = []

    def observe(event, *args, **payload):
        if event == "subagent.spawn_requested":
            published.append(agent._session_db.get_session(payload["child_session_id"]))

    agent.tool_progress_callback = observe
    child = _build(agent)
    try:
        assert len(published) == 1 and published[0] is not None
        assert published[0]["source"] == "subagent"
        assert published[0]["parent_session_id"] == agent.session_id
        assert published[0]["model_config"] is None
        assert not child._session_db_created
        admission = admit_durable_turn_lease(child, session_id=child.session_id, relay_turn_id="synthetic-turn",
                                             task_context={"session_id": child.session_id, "platform": "subagent"},
                                             conversation_history=None)
        assert admission.lease is not None
        try:
            assert not child._session_db_created
            child._session_init_model_config["synthetic_setting"] = "ready"
            child._cached_system_prompt = "Synthetic completed prompt"
            child._ensure_db_session()
            ready = agent._session_db.get_session(child.session_id)
            assert json.loads(ready["model_config"])["synthetic_setting"] == "ready"
            assert json.loads(ready["model_config"])["_delegate_from"] == agent.session_id
            assert ready["system_prompt"] == "Synthetic completed prompt"
            assert ready["model"] == "synthetic-model"
        finally:
            admission.lease.release()
        assert not (home / "state.db").read_bytes().startswith(b"SQLite format 3")
    finally:
        child.close()
    assert agent._session_db.get_session(agent.session_id) is not None


def test_failed_child_reservation_is_not_published_and_can_be_retried(parent, monkeypatch):
    from hermes_state import SessionDB

    agent, _ = parent
    published = []
    agent.tool_progress_callback = lambda event, *args, **payload: published.append(event)
    real_create = SessionDB.create_session

    def unavailable(database, *args, **kwargs):
        if kwargs.get("source") == "subagent":
            raise OSError("synthetic reservation failure")
        return real_create(database, *args, **kwargs)

    with monkeypatch.context() as blocked:
        blocked.setattr(SessionDB, "create_session", unavailable)
        with pytest.raises(OSError, match="synthetic reservation failure"):
            _build(agent)
    assert "subagent.spawn_requested" not in published
    assert agent._session_db.get_session(agent.session_id) is not None
    child = _build(agent)
    try:
        assert agent._session_db.get_session(child.session_id) is not None
        assert "subagent.spawn_requested" in published
    finally:
        child.close()
