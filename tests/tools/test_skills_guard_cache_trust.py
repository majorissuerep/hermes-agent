"""Cached scan policy follows current source trust without losing exact cache hits.

Pure helper evidence with synthetic, never-executed skill content; no network or app.
"""
from io import StringIO

import pytest
from rich.console import Console

from tools import skills_guard
from tools.skills_hub_models import SkillBundle

SOURCE = "synthetic-vendor/skills/caution-skill"
REPO = "synthetic-vendor/skills"


def _skill(tmp_path):
    skill = tmp_path / "caution-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Synthetic caution skill\n", encoding="utf-8")
    (skill / "example.py").write_text("eval('1 + 1')\n", encoding="utf-8")
    return skill


@pytest.mark.parametrize("initial_trusted", [True, False])
def test_cached_scan_reclassifies_both_directions_and_reuses_unchanged_policy(tmp_path, monkeypatch, initial_trusted):
    skill, cache = _skill(tmp_path), tmp_path / "scan-cache"
    monkeypatch.setattr(skills_guard, "TRUSTED_REPOS", {REPO} if initial_trusted else set())
    first, first_provenance = skills_guard.scan_skill_cached(skill, source=SOURCE, cache_dir=cache)
    same, same_provenance = skills_guard.scan_skill_cached(skill, source=SOURCE, cache_dir=cache)
    assert first_provenance["fresh"] is True and same_provenance["fresh"] is False
    assert same.verdict == first.verdict == "caution"
    assert skills_guard.should_allow_install(same)[0] is initial_trusted
    before = {path.name: path.read_bytes() for path in skill.iterdir()}

    monkeypatch.setattr(skills_guard, "TRUSTED_REPOS", set() if initial_trusted else {REPO})
    current, current_provenance = skills_guard.scan_skill_cached(skill, source=SOURCE, cache_dir=cache)
    assert current_provenance["fresh"] is True
    assert current.trust_level == ("community" if initial_trusted else "trusted")
    assert current.verdict == "caution"
    assert skills_guard.should_allow_install(current)[0] is (not initial_trusted)
    again, again_provenance = skills_guard.scan_skill_cached(skill, source=SOURCE, cache_dir=cache)
    assert again_provenance["fresh"] is False
    assert again.trust_level == current.trust_level
    assert {path.name: path.read_bytes() for path in skill.iterdir()} == before


def test_cli_quarantine_rescan_blocks_previously_trusted_caution(tmp_path, monkeypatch):
    from hermes_cli.skills_hub import _scan_quarantined
    from tools import skills_hub

    monkeypatch.setattr(skills_hub, "HUB_DIR", tmp_path / "hub")
    monkeypatch.setattr(skills_guard, "TRUSTED_REPOS", {REPO})
    skill = _skill(tmp_path)
    bundle = SkillBundle(skill.name, {}, "github", SOURCE, "trusted")
    output = StringIO()
    console = Console(file=output, width=120)
    old = _scan_quarantined(console, skill, bundle, None, SOURCE)
    assert skills_guard.should_allow_install(old)[0] is True

    monkeypatch.setattr(skills_guard, "TRUSTED_REPOS", set())
    current = _scan_quarantined(console, skill, bundle, None, SOURCE)
    assert current.trust_level == "community" and current.verdict == "caution"
    assert skills_guard.should_allow_install(current)[0] is False
    assert current.scan_provenance["fresh"] is True
    assert "BLOCKED" in output.getvalue()
