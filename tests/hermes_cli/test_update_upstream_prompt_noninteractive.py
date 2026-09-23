"""Fork: upstream synchronization is REMOVED.

Upstream asked ``Add official repo as 'upstream' remote?`` on fork checkouts
(#60240). This fork pins the opposite contract: the updater NEVER contacts
NousResearch/hermes-agent — no prompt (interactive or not), no remote
mutation, no marker files, no fetch. Origin is the only update source.
"""

from unittest.mock import patch

from hermes_cli import update_cmd, update_cmd_git


def test_sync_with_upstream_is_a_hard_noop(tmp_path):
    """_sync_with_upstream_if_needed never prompts, never touches git."""
    with patch("builtins.input") as stdin_input, patch.object(
        update_cmd, "_add_upstream_remote"
    ) as add_remote, patch.object(
        update_cmd, "_mark_skip_upstream_prompt"
    ) as mark_skip:
        result = update_cmd_git._sync_with_upstream_if_needed(["git"], tmp_path)
    assert result is False
    stdin_input.assert_not_called()
    add_remote.assert_not_called()
    mark_skip.assert_not_called()


def test_update_fetch_prefers_origin_even_with_upstream_remote(monkeypatch):
    """The update fetch consults origin regardless of a leftover upstream remote."""
    import inspect

    src = inspect.getsource(update_cmd)
    # The upstream-probe branch is gone: 'Fetching from upstream' must not
    # appear in the fetch logic.
    assert "Fetching from upstream" not in src


def test_offer_upstream_remote_never_runs_from_update():
    """_offer_upstream_remote still exists (dead code kept for API compat) but
    is unreachable from the update flow: _sync_with_upstream_if_needed
    returns before it."""
    import inspect

    body = inspect.getsource(update_cmd_git._sync_with_upstream_if_needed)
    assert "return False" in body
    assert "_offer_upstream_remote" not in body
