"""Regression tests: profile HERMES_HOME override in ephemeral agent threads (#50233).

Why: normal prompt turns bind ``session['profile_home']`` via
``set_hermes_home_override`` before ``run_conversation`` so the turn runs against
the correct profile home. The ``prompt.background`` path spawns a fresh ``AIAgent``
on a NEW thread, and the
``HERMES_HOME`` ContextVar set on the session-create thread does NOT propagate to
those threads. Without an explicit re-bind, a background turn under
a non-default profile would run against the wrong home. This module locks in:

  1. ``prompt.background`` re-binds ``profile_home`` for the ephemeral turn.
  2. The worker RESTORES the override after the turn (reset token from set), exactly
     like the normal prompt turn, and skip the bind entirely when no profile is set.

Each test drives the real RPC handler
from ``tui_runtime.server._methods`` with ``threading.Thread`` patched to run the
target inline, then asserts on the recorded override set/reset calls and the agent.
"""

from unittest.mock import MagicMock, patch

import pytest

from tui_runtime import server as srv

PROFILE_HOME = "/home/user/.hermes/profiles/work"

class _InlineThread:
    """Drop-in for ``threading.Thread`` that runs the target synchronously.

    Why: the ephemeral RPC handlers do their real work inside ``run()`` on a
    spawned thread; running it inline makes the override set/reset observable
    within the test without racing a real background thread.
    """

    def __init__(self, target=None, daemon=None, **_kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()

@pytest.fixture
def fake_session():
    """A minimal session carrying a non-default ``profile_home``."""
    agent = MagicMock()
    return {"agent": agent, "session_key": "sess_k", "profile_home": PROFILE_HOME}

@pytest.fixture
def override_calls():
    """Patch set/reset override + AIAgent + emit/context helpers; record calls.

    Returns a dict with the mocks so each test can assert on set/reset ordering
    and on whether the ephemeral agent was closed.
    """
    agent_instance = MagicMock()
    # run_conversation returns a plain dict like the real agent does.
    agent_instance.run_conversation.return_value = {"final_response": "done"}

    with patch("tui_runtime.server.threading.Thread", _InlineThread), \
        patch("tui_runtime.server.set_hermes_home_override", return_value="TOK") as m_set, \
        patch("tui_runtime.server.reset_hermes_home_override") as m_reset, \
        patch("tui_runtime.server._background_agent_kwargs", return_value={}), \
        patch("tui_runtime.server._set_session_context", return_value=None), \
        patch("tui_runtime.server._clear_session_context"), \
        patch("tui_runtime.server._session_cwd", return_value="/tmp"), \
        patch("tui_runtime.server._emit"), \
        patch("run_agent.AIAgent", return_value=agent_instance) as m_agent:
        yield {
            "set": m_set,
            "reset": m_reset,
            "agent_cls": m_agent,
            "agent": agent_instance,
        }

def _run(method_name, params, session):
    """Invoke a registered RPC handler with ``_sess`` patched to our session."""
    handler = srv._methods[method_name]
    with patch("tui_runtime.server._sess", return_value=(session, None)):
        return handler("rid1", params)

class TestBackgroundProfileOverride:
    def test_background_binds_and_restores_profile_home(self, fake_session, override_calls):
        """prompt.background binds profile_home for the ephemeral turn and restores it."""
        _run("prompt.background", {"text": "hi", "session_id": "ui1"}, fake_session)

        override_calls["set"].assert_called_once_with(PROFILE_HOME)
        override_calls["reset"].assert_called_once_with("TOK")
        override_calls["agent"].run_conversation.assert_called_once()

    def test_background_restores_override_on_error(self, fake_session, override_calls):
        """A failing turn must still restore the override (finally-block parity)."""
        override_calls["agent"].run_conversation.side_effect = RuntimeError("boom")
        _run("prompt.background", {"text": "hi", "session_id": "ui1"}, fake_session)

        override_calls["set"].assert_called_once_with(PROFILE_HOME)
        override_calls["reset"].assert_called_once_with("TOK")
