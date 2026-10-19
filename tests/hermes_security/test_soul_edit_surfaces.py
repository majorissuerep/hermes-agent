"""Identity editing surfaces must preserve envelope semantics, including profile publish."""
from pathlib import Path

from hermes_security import io, vault


def test_profile_identity_writes_preserve_encryption_a_b_a(tmp_path):
    from hermes_cli.profiles_soul import write_profile_soul

    homes = {name: tmp_path / name for name in ("alpha", "beta")}
    for name, home in homes.items():
        vault.init_vault(home, "synthetic-soul-edit")
        vault.unlock(home, "synthetic-soul-edit")
        io.write_text(home / "config.yaml", "{}", purpose="config")
        io.write_text(home / "SOUL.md", name, purpose="state")
    try:
        for index, name in enumerate(("alpha", "beta", "alpha")):
            home = homes[name]
            sibling = homes["beta" if name == "alpha" else "alpha"] / "SOUL.md"
            untouched = sibling.read_bytes()
            write_profile_soul(home, f"edit {index}")
            assert io.looks_like_envelope(home / "SOUL.md")
            assert io.read_text(home / "SOUL.md", purpose="state") == f"edit {index}"
            assert sibling.read_bytes() == untouched
    finally:
        vault.clear_vault_cache()


def test_new_profile_soul_is_sealed_for_published_path(tmp_path, monkeypatch):
    from hermes_cli import profiles
    from hermes_cli.default_soul import DEFAULT_SOUL_MD
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(root))
    vault.init_vault(root, "synthetic-profile-create")
    vault.unlock(root, "synthetic-profile-create")
    try:
        created = profiles.create_profile("sealed-child", no_skills=True, no_alias=True)
        assert io.looks_like_envelope(created / "SOUL.md")
        assert io.read_text(created / "SOUL.md", purpose="state") == DEFAULT_SOUL_MD
    finally:
        vault.clear_vault_cache()
