"""Upstream inventories preserve dirty work and distinguish local and excluded areas."""
import subprocess

from scripts.ci.upstream_review import inventory


def _git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True, encoding="utf-8", timeout=20).strip()


def test_inventory_pins_source_and_preserves_the_checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.test")
    (repo / "base").write_text("base", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    original = _git(repo, "rev-parse", "HEAD")
    _git(repo, "branch", "fork")
    for name in ("tools/local.py", "gateway/remote.py"):
        path = repo / name
        path.parent.mkdir()
        path.write_text("review input", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "new upstream tools")
    source = _git(repo, "rev-parse", "HEAD")
    (repo / "base").write_text("operator's uncommitted work", encoding="utf-8")
    before = _git(repo, "status", "--porcelain")
    result = inventory(repo, fork="fork", upstream="HEAD")
    assert (result["fork"], result["upstream"], result["shared_base"]) == (original, source, original)
    assert result["upstream_commits"] == 1
    assert result["area_counts"]["tools/"] == result["area_counts"]["gateway/"] == 1
    assert result["local_port_candidates"] == [{"commit": source, "subject": "new upstream tools"}]
    assert _git(repo, "status", "--porcelain") == before
    assert (repo / "base").read_text(encoding="utf-8") == "operator's uncommitted work"
