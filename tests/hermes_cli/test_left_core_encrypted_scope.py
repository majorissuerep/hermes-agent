"""Migration predicates obey real encrypted profile state and managed policy."""
from hermes_cli import left_core_migration as migration


def test_encrypted_plugin_selection_stays_bound_to_its_profile(tmp_path, monkeypatch):
    from hermes_constants import get_hermes_home
    from tests.e2e.vault_home import seal_fixture_home

    homes = [tmp_path / name for name in ("A", "B")]
    for home, selection in zip(homes, ("[langfuse]", "[]")):
        home.mkdir()
        (home / "config.yaml").write_text(f"plugins:\n  enabled: {selection}\n")
        seal_fixture_home(home)
    monkeypatch.setenv("HERMES_HOME", str(homes[1]))
    before = {home: (home / "config.yaml").read_bytes() for home in homes}
    assert migration.langfuse_in_use(homes[0]) is True
    assert migration.langfuse_in_use(homes[1]) is False
    assert get_hermes_home() == homes[1]
    assert {home: (home / "config.yaml").read_bytes() for home in homes} == before


def test_managed_plugin_selection_overrides_encrypted_user_selection(tmp_path, monkeypatch):
    from tests.e2e.vault_home import seal_fixture_home

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("plugins:\n  enabled: []\n")
    seal_fixture_home(home)
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "config.yaml").write_text("plugins:\n  enabled: [langfuse]\n")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    assert migration.langfuse_in_use(home) is True


def test_encrypted_native_tool_login_does_not_borrow_launch_profile(tmp_path, monkeypatch):
    from tests.e2e.vault_home import seal_fixture_home

    owner = tmp_path / "owner"
    other = tmp_path / "other"
    owner.mkdir()
    other.mkdir()
    (owner / "auth.json").write_text('{"providers":{"spotify":{"access_token":"synthetic-login"}}}')
    (other / "auth.json").write_text('{"providers":{}}')
    for home in (owner, other):
        seal_fixture_home(home)
    monkeypatch.setenv("HERMES_HOME", str(other))
    before = (owner / "auth.json").read_bytes()
    assert migration.spotify_in_use(owner) is True
    assert migration.spotify_in_use(other) is False
    assert (owner / "auth.json").read_bytes() == before
