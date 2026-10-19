"""Real encrypted state for outgoing OAuth storage and refresh contracts."""

import pytest

from hermes_security import io as state_io


@pytest.fixture(autouse=True)
def oauth_home(tmp_path, monkeypatch):
    from hermes_security import vault
    from tests.e2e.vault_home import seal_fixture_home

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    seal_fixture_home(tmp_path)
    yield
    vault.lock_now(tmp_path)


def read_oauth_state(path, encoding="utf-8"):
    return state_io.read_text(path, purpose="state", encoding=encoding)


def write_oauth_state(path, text, encoding="utf-8"):
    return state_io.write_text(path, text, purpose="state", encoding=encoding)
