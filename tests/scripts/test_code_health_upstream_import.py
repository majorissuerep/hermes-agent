"""A reviewed upstream import carries its debt, never a license for new debt."""

from scripts.ci import upstream_sync_base
from scripts.code_health.cli import run
from tests.scripts.test_code_health import _commit, _git, _repo, _SWALLOW


def _merged(tmp_path, monkeypatch):
    repo, shared = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "upstream")
    imported = _commit(repo, {"pkg/imported.py": _SWALLOW})
    monkeypatch.setattr(upstream_sync_base, "UPSTREAM", imported)
    _git(repo, "checkout", "-q", "-b", "fork", shared)
    base = _commit(repo, {"pkg/fork.py": _SWALLOW.replace("other", "fork_legacy")})
    _git(repo, "merge", "-q", "--no-ff", "upstream", "-m", "reviewed import")
    return repo, base


def test_import_preserves_both_ancestors_but_new_occurrences_still_block(tmp_path, monkeypatch, capsys):
    repo, base = _merged(tmp_path, monkeypatch)
    assert run(repo, base, None) == 0, capsys.readouterr().out
    # A copied violation is new even when its original arrived from upstream.
    _commit(repo, {"pkg/copied.py": _SWALLOW})
    assert run(repo, base, None) == 1
    output = capsys.readouterr().out
    assert "pkg/copied.py" in output and "BLE001" in output
    # Fixing the import does not buy a different violation in that same function.
    _commit(repo, {"pkg/copied.py": None, "pkg/imported.py": _SWALLOW.replace("except Exception:", "except BaseException:")})
    assert run(repo, base, None) == 1
    assert "pkg/imported.py" in capsys.readouterr().out


def test_once_main_contains_import_its_improvements_are_locked_in(tmp_path, monkeypatch, capsys):
    repo, _ = _merged(tmp_path, monkeypatch)
    base = _commit(repo, {"pkg/imported.py": "def other():\n    return 1\n"})
    _commit(repo, {"pkg/imported.py": _SWALLOW})
    assert run(repo, base, None) == 1
    output = capsys.readouterr().out
    assert "pkg/imported.py" in output and "BLE001" in output
