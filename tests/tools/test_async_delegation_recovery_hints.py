"""Recovery tails authenticate the selected profile and never return ciphertext bytes."""
from pathlib import Path

from tools.async_delegation_recovery_hints import transcript_tail


def test_recovery_tail_is_bounded_and_refuses_locked_corrupt_and_foreign_logs(tmp_path, monkeypatch):
    from hermes_security import vault
    from hermes_security.delegation_state import DelegationStore
    from tests.e2e.vault_home import fixture_password, seal_fixture_home

    home, foreign = tmp_path / "profile", tmp_path / "foreign"
    monkeypatch.setenv("HERMES_HOME", str(home))
    for selected in (home, foreign):
        selected.mkdir()
        seal_fixture_home(selected)
    relative = Path("cache/delegation/live/deleg_tail/task-0.log")
    own_path, foreign_path = home / relative, foreign / relative
    own = DelegationStore(home)
    own.create_log(own_path, "kickoff\n" + "\n".join(f"row-{i}: " + "x" * 300 for i in range(40)))
    DelegationStore(foreign).create_log(foreign_path, "foreign-private-kickoff")
    original, other = own_path.read_bytes(), foreign_path.read_bytes()
    tail = transcript_tail(str(own_path))
    assert tail and len(tail) <= 2_000 and len(tail.splitlines()) <= 20
    assert "row-39" in tail and "row-0:" not in tail
    assert transcript_tail(str(foreign_path)) is None
    vault.lock_now(home)
    assert transcript_tail(str(own_path)) is None
    vault.unlock(home, fixture_password(home))
    assert transcript_tail(str(own_path)) == tail
    assert own_path.read_bytes() == original and foreign_path.read_bytes() == other
    damaged = original[:-1] + bytes([original[-1] ^ 1])
    own_path.write_bytes(damaged)
    assert transcript_tail(str(own_path)) is None
    assert own_path.read_bytes() == damaged and foreign_path.read_bytes() == other
    assert vault.is_unlocked(home) and vault.is_unlocked(foreign)
