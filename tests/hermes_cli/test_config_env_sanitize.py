"""Migration must normalize decrypted secrets, never ciphertext as text."""
import pytest


def test_encrypted_migration_preserves_each_homes_secrets_and_rejects_locked_rewrites(tmp_path, monkeypatch):
    from agent.secret_scope import (build_profile_secret_scope, set_secret_scope, reset_secret_scope,
                                    set_multiplex_context, reset_multiplex_context)
    from hermes_cli.config import load_env, sanitize_env_file
    from hermes_security import io, vault
    from hermes_security.errors import VaultLockedError

    homes = [tmp_path / name for name in ("A", "B")]
    try:
        for home in homes:
            monkeypatch.setenv("HERMES_HOME", str(home))
            vault.init_vault(home, "synthetic-env-migration-password")
            vault.unlock(home, "synthetic-env-migration-password")
            io.write_text(home / ".env", f"  E2E_RETAINED_SECRET=secret-{home.name}  \n"
                          "OTHER_SECRET=opaqueGLM_API_KEY=not-an-assignment\n", purpose="env")
        for home in (homes[0], homes[1], homes[0]):
            monkeypatch.setenv("HERMES_HOME", str(home))
            scope = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
            multiplex = set_multiplex_context(True)
            try:
                before = load_env()
                sanitize_env_file()
                assert load_env() == before
                assert before["E2E_RETAINED_SECRET"] == f"secret-{home.name}"
                assert (home / ".env").read_bytes().startswith(b"HRMVAULT")
                sealed = (home / ".env").read_bytes()
                assert sanitize_env_file() == 0
                assert (home / ".env").read_bytes() == sealed
            finally:
                reset_multiplex_context(multiplex)
                reset_secret_scope(scope)
        vault.clear_vault_cache()
        sealed = (home / ".env").read_bytes()
        with pytest.raises(VaultLockedError):
            sanitize_env_file()
        assert (home / ".env").read_bytes() == sealed
    finally:
        vault.clear_vault_cache()
