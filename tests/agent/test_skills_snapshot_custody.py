"""Owned skill indexes retain vault custody across builds and reloads."""
from __future__ import annotations

import pytest

from agent import prompt_builder as prompts
from hermes_security import io, vault
from hermes_security.errors import VaultError


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    selected.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, "synthetic snapshot password")
    vault.unlock(selected, "synthetic snapshot password")
    prompts.clear_skills_system_prompt_cache()
    yield selected
    prompts.clear_skills_system_prompt_cache()
    vault.clear_vault_cache()


def test_private_snapshot_build_and_cold_reload_stay_encrypted(home):
    skills = home / "skills"
    skill = skills / "private" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    # Installed skill code is public; its derived, owned index must still be sealed.
    description = "synthetic private project description 4c02"
    skill.write_text(f"---\nname: private\ndescription: {description}\n---\nPublic fixture.\n")
    first = prompts.build_skills_system_prompt(skills_dir_override=skills)
    assert description in first
    target = home / ".skills_prompt_snapshot.json"
    assert io.looks_like_envelope(target)
    assert description.encode() not in target.read_bytes()
    prompts.clear_skills_system_prompt_cache()
    assert prompts.build_skills_system_prompt(skills_dir_override=skills) == first
    assert io.read_json(target, purpose="state")["skills"][0]["description"] == description


@pytest.mark.parametrize("failure", ["locked", "damaged"])
def test_unreadable_sealed_snapshot_is_not_treated_as_missing(home, failure):
    target = home / ".skills_prompt_snapshot.json"
    io.write_json(target, {"version": prompts._SKILLS_SNAPSHOT_VERSION, "manifest": {}}, purpose="state")
    if failure == "locked":
        vault.lock_now(home)
    else:
        blob = bytearray(target.read_bytes())
        blob[-1] ^= 1
        target.write_bytes(blob)
    before = target.read_bytes()
    with pytest.raises(VaultError):
        prompts._load_skills_snapshot(home / "skills", manifest={})
    assert target.read_bytes() == before
