"""Doctor inspects and repairs the same sealed files used by the agent."""
import pytest

from hermes_security import io, vault


@pytest.fixture
def home(tmp_path, monkeypatch):
    from hermes_cli import doctor

    target = tmp_path / "home"
    target.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(target))
    monkeypatch.setattr(doctor, "HERMES_HOME", target)
    vault.init_vault(target, "doctor-vault-password")
    vault.unlock(target, "doctor-vault-password")
    io.write_text(target / "config.yaml", "memory: {memory_enabled: true, user_profile_enabled: true}\n", purpose="config")
    try:
        yield target
    finally:
        vault.clear_vault_cache()


def test_doctor_reads_encrypted_credentials_persona_and_memory(home, capsys):
    from hermes_cli.doctor_config import _check_env_file
    from hermes_cli.doctor_state import _check_directory_structure

    io.write_text(home / ".env", "OPENAI_API_KEY=doctor-private-canary\n", purpose="env")
    io.write_text(home / "SOUL.md", "Persona private canary", purpose="state")
    memory = "Memory private canary"
    io.write_text(home / "memories/MEMORY.md", memory, purpose="memory")
    assert not _check_env_file(False).issues
    assert not _check_directory_structure(False).issues
    output = capsys.readouterr().out
    assert "API key or custom endpoint configured" in output
    assert "persona configured" in output and f"{len(memory)} chars" in output
    assert "private-canary" not in output and "private canary" not in output


def test_doctor_repairs_missing_files_as_envelopes_and_refuses_locked_state(home):
    from hermes_cli.doctor_config import _check_env_file
    from hermes_cli.doctor_state import _check_directory_structure
    from hermes_security.errors import VaultLockedError

    assert _check_env_file(True).fixed == 1
    assert _check_directory_structure(True).fixed >= 1
    assert io.read_text(home / ".env", purpose="env") == ""
    assert "Hermes" in io.read_text(home / "SOUL.md", purpose="state")
    before = {path: path.read_bytes() for path in (home / ".env", home / "SOUL.md")}
    assert all(data.startswith(b"HRMVAULT\x00") for data in before.values())
    vault.lock_now(home)
    with pytest.raises(VaultLockedError):
        _check_env_file(True)
    assert {path: path.read_bytes() for path in before} == before
