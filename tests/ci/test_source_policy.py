"""The common CI command retains the old critical-source and artifact refusals."""
import subprocess

from scripts.ci import check_source_policy as policy


def git(repo, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=repo, text=True, timeout=10).strip()


def init(repo):
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "commit", "--allow-empty", "-qm", "initial")
    return git(repo, "rev-parse", "HEAD")


def commit(repo):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture change")
    return git(repo, "rev-parse", "HEAD")


def test_shared_guard_refuses_payloads_unbounded_dependencies_and_tracked_infographics(tmp_path, monkeypatch, capsys):
    base = init(tmp_path)
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    encoded = "base64.b64decode('c3ludGhldGlj')"
    encoded_argument = "chr(42))\n"
    payload = "exec(" + encoded + ")\n" + "subprocess.run(" + encoded_argument
    (tmp_path / "module.py").write_text(payload, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('dependencies = ["example>=1.0"]\n', encoding="utf-8")
    image = tmp_path / "misc" / "infograficos" / "pr.webp"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"synthetic image")
    head = commit(tmp_path)
    assert policy.main(["--base", base, "--head", head]) == 1
    output = capsys.readouterr().out
    assert all(rule in output for rule in ("decoded-code-execution", "encoded-subprocess",
                                           "unbounded-dependency", "committed-infographic"))
    assert encoded not in output
    (tmp_path / "module.py").write_text("print('ordinary source')\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('dependencies = ["example>=1.0,<2"]\n', encoding="utf-8")
    image.unlink()
    assert policy.main(["--base", base, "--head", commit(tmp_path)]) == 0


def test_existing_suspicious_lines_are_not_new_source_findings(tmp_path, monkeypatch):
    init(tmp_path)
    encoded_argument = "chr(42))\n"
    (tmp_path / "legacy.py").write_text("subprocess.run(" + encoded_argument, encoding="utf-8")
    base = commit(tmp_path)
    (tmp_path / "ordinary.py").write_text("print('new ordinary code')\n", encoding="utf-8")
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    assert policy.main(["--base", base, "--head", commit(tmp_path)]) == 0
    assert policy.main(["--base", "missing-base"]) == 2
